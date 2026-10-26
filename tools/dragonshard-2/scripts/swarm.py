#!/usr/bin/env python3
"""One dependency queue for all binaries: fill available capacity with any ready useful task."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from dragonshard import GIB, HERE, analysis_blockers, atomic_json, digest, locks, source_state
from campaign import available, checked_batch, size


def ready_jobs(jobs):
    """Dependencies belong to one binary/task, never an entire priority batch."""
    return sorted((j for j in jobs.values() if j['state'] == 'pending'
                   and all(jobs[d]['state'] == 'complete' for d in j['deps'])),
                  key=lambda j: (j['priority'], -j.get('weight', 0), j['id']))


def select_jobs(ready, *, cpu_slots, memory_gib, disk_gib):
    """Backfill small ready work when a larger job cannot fit; never head-of-line block."""
    selected, blocked = [], {}
    for job in ready:
        reason = ('cpu_capacity' if job['cpu'] > cpu_slots else
                  'memory_reserve' if job['memory_gib'] > memory_gib else
                  'disk_reserve' if job['disk_gib'] > disk_gib else None)
        if reason:
            blocked[job['id']] = reason
            continue
        selected.append(job)
        cpu_slots -= job['cpu']
        memory_gib -= job['memory_gib']
        disk_gib -= job['disk_gib']
    return selected, blocked


def process_group_memory(pgids):
    totals = dict.fromkeys(pgids, 0)
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            group = int(stat[2])
            if group in totals:
                totals[group] += int(stat[21]) * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, IndexError):
            continue
    return {group: total / GIB for group, total in totals.items()}


def cgroup_memory_headroom():
    """The systemd service limit can be lower than the machine's available memory."""
    relative = next((s[3:] for s in Path('/proc/self/cgroup').read_text().splitlines()
                     if s.startswith('0::')), None)
    if relative is None:
        return math.inf
    base = Path('/sys/fs/cgroup')
    group = base / relative.lstrip('/')
    headroom = math.inf
    while group != base:
        for name in ('memory.high', 'memory.max'):
            value = (group / name).read_text().strip()
            if value.isdigit():
                current = int((group / 'memory.current').read_text())
                headroom = min(headroom, max(0, int(value) - current) / GIB)
        group = group.parent
    return headroom


class Swarm:
    def __init__(self, manifest, root, *, adopt=None, cpu_limit=None, disk_reserve_gib=0, adopt_queue=None):
        self.manifest, self.root, self.adopt = manifest, root, adopt
        self.cpu_limit = cpu_limit or os.cpu_count() or 1
        self.jobs, self.programs, self.active = {}, {}, {}
        self.imported_databases = []
        self.stop = False
        self.last_sage = 0
        self.cpu_sample = None
        self.cpu_percent = None
        self.queue_version = 0
        self.persisted_version = -1
        self.external_checked_at = 0
        self.external_reserved_gib = 0
        self.disk_reserve_gib = disk_reserve_gib
        self.adopt_queue = adopt_queue

    def persist(self, state='running', reason=None, blocked=None):
        programs = list(self.programs.values())
        counts = Counter(j['state'] for j in self.jobs.values())
        if self.persisted_version != self.queue_version:
            atomic_json(self.root / 'queue.json', {'programs': programs, 'jobs': self.jobs})
            self.persisted_version = self.queue_version
        ready = ready_jobs(self.jobs)
        atomic_json(self.root / 'status.json', {
            'state': state, 'reason': reason, 'programs_total': len(programs),
            'programs_indexed': sum(p['state'] == 'complete' for p in programs),
            'programs_gap_audited': sum(bool(p.get('gap_audit')) for p in programs),
            'indirect_shards_complete': sum(j['kind'] == 'indirect' and j['state'] == 'complete' for j in self.jobs.values()),
            'indirect_shards_total': sum(j['kind'] == 'indirect' for j in self.jobs.values()),
            'jobs': dict(counts), 'ready_tasks': len(ready), 'active_tasks': len(self.active),
            'cpu_percent': self.cpu_percent, 'cpu_slots_limit': self.cpu_limit,
            'disk_reserve_gib': self.disk_reserve_gib,
            'idle_reasons': dict(Counter((blocked or {}).values())),
            'external_dependencies': [p['name'] for p in programs if p['state'] == 'waiting_external'],
            'active': [{'id': j, 'pid': proc.pid, 'kind': self.jobs[j]['kind'],
                        'program': self.jobs[j]['program']['name']} for j, proc in self.active.items()]})
        databases = list(self.imported_databases)
        databases += [p['database'] for p in programs if p.get('database') and p['database'] not in databases]
        atomic_json(self.root / 'catalog.json', {'schema': 1, 'complete': self.finished(),
                                               'databases': databases})

    def event(self, event, **values):
        with (self.root / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(time=time.time(), event=event, **values)) + '\n')

    def add_job(self, program, kind, deps=(), **extra):
        resources = {'stage': (1, 3, .15), 'analyze': (4, 6, .5),
                     'warm': (1, 1.5, 2),
                     'export': (1, 1.5, .0625), 'index': (1, 2, .25), 'gaps': (1, 1.5, .0625),
                     'indirect': (1, 1.5, .0625)}
        cpu, memory, disk = resources[kind]
        cpu = min(cpu, self.cpu_limit)
        if kind == 'analyze' and program['functions'] > 100000:
            memory = 18
        if kind in ('export', 'gaps', 'indirect') and program['functions'] <= 100000:
            memory = 1.25  # prior 31-worker DLL run peaked at 15.5 GiB total; retain native-memory allowance
        if extra.get('packed_file') and kind in ('export', 'gaps', 'indirect'):
            # Previous 42-worker agent export peaked at 34.8 GiB in its cgroup.
            # Indirect SSA tracing has separately measured larger native helpers.
            memory = 3 if kind == 'indirect' else 1.25
        priority = {'analyze': 0, 'stage': 0, 'warm': 0, 'index': 1, 'export': 2, 'gaps': 2, 'indirect': 2}[kind]
        if kind in ('export', 'gaps', 'indirect') and not extra.get('packed_file'):
            # Reflink shares blocks initially, but budget full potential clone divergence.
            disk += size(Path(extra['source_dir']) / (extra['source_name'] + '.rep')) / GIB
        identifier = program['sha256'][:16] + '.' + kind + extra.pop('suffix', '')
        task_manifest = {k: v for k, v in self.manifest.items() if k != 'programs'}
        job = dict(id=identifier, kind=kind, state='pending', deps=list(deps), priority=priority,
                   weight=program['functions'], cpu=cpu, memory_gib=memory, disk_gib=disk,
                   manifest=task_manifest, program=program,
                   work=str(self.root / 'programs' / program['sha256'][:16]), **extra)
        self.jobs[identifier] = job
        self.queue_version += 1
        return identifier

    def plan(self):
        completed = {}
        old_gaps = {}
        prior_jobs = {}
        if self.adopt_queue:
            old = json.loads((self.adopt_queue / 'queue.json').read_text())
            prior_jobs = old['jobs']
            for job in old['jobs'].values():
                if job['kind'] == 'gaps' and job['state'] == 'complete':
                    old_gaps[job['program']['sha256']] = job['result']
        if self.adopt:
            status = json.loads((self.adopt / 'status.json').read_text())
            for batch in status.get('batches', []):
                directory = self.adopt / f"batch{batch['batch']}"
                expected = json.loads((directory / 'run/manifest.json').read_text())['programs']
                report = checked_batch(directory, expected)
                database = str(directory / 'run/corpus.sqlite')
                self.imported_databases.append(database)
                for row in report:
                    completed[row['sha256']] = (database, row, expected,
                        directory / 'source', f"DeepBatch{batch['batch']}")
        for program in self.manifest['programs']:
            sha = program['sha256']
            p = dict(sha256=sha, name=program['name'], program=program, state='pending')
            self.programs[sha] = p
            if sha in completed:
                p.update(state='complete', database=completed[sha][0], coverage=completed[sha][1], adopted=True)
                _, _, expected, source_dir, source_name = completed[sha]
                if not (source_dir / (source_name + '.gpr')).exists():
                    raise RuntimeError('Adopted source project missing')
                original = next(row for row in expected if row['sha256'] == sha)
                identifier = self.add_gaps(original, source_dir, source_name)
                if sha in old_gaps:
                    result = old_gaps[sha]
                    from swarm_tasks import evidence_summary
                    evidence_summary(Path(result['output']), sha, self.jobs[identifier]['snapshot_id'])
                    self.jobs[identifier].update(state='complete', result=result, adopted=True)
                    p['gap_audit'] = result
                    self.add_indirect(p, self.jobs[identifier], result)
                    for task_id in p['indirect_jobs']:
                        job, prior = self.jobs[task_id], prior_jobs.get(task_id, {})
                        if (prior.get('state') == 'complete' and prior.get('snapshot_id') == job['snapshot_id']
                                and prior.get('functions') == job['functions']):
                            evidence_summary(Path(prior['result']['output']), sha, job['snapshot_id'])
                            job.update(state='complete', result=prior['result'], adopted=True)
                continue
            if digest(program['binary_path']) != sha:
                raise RuntimeError(f'Binary changed: {program["name"]}')
            ahead = self.adopt / 'ahead' / sha[:16] if self.adopt else None
            if ahead and (ahead / 'owner.json').exists():
                owner = json.loads((ahead / 'owner.json').read_text())
                if owner.get('tool') != 'dragonshard-2-ahead' or owner['program']['sha256'] != sha:
                    raise RuntimeError('Ahead task ownership mismatch')
                p.update(state='waiting_external', external_dir=str(ahead),
                         external_unit=json.loads((self.adopt / 'ahead-service.json').read_text())['unit'],
                         source_name=f"Batch{owner['batch']}")
            else:
                stage = self.add_job(program, 'stage')
                self.add_job(program, 'analyze', [stage], heap='12G' if program['functions'] > 100000 else '4G')
                p['state'] = 'queued'
        self.queue_version += 1

    def add_gaps(self, program, source_dir, source_name, *, packed=None, deps=()):
        if packed:
            return self.add_job(program, 'gaps', deps, source_dir=str(source_dir), source_name=source_name,
                                snapshot_id=packed['packed_sha256'], output_limit_bytes=512 * 1024**2, **packed)
        source = dict(self.manifest, project_dir=str(source_dir), project_name=source_name)
        if locks(source):
            raise RuntimeError('Gap audit source is still locked')
        snapshot = hashlib.sha256(json.dumps(source_state(source), separators=(',', ':')).encode()).hexdigest()
        return self.add_job(program, 'gaps', source_dir=str(source_dir), source_name=source_name, snapshot_id=snapshot,
                            output_limit_bytes=128 * 1024**2)

    def add_indirect(self, p, gap_job, result):
        functions, unowned = {}, 0
        with Path(result['output']).open() as stream:
            for line in stream:
                row = json.loads(line)
                if row['kind'] != 'unresolved_computed_transfer':
                    continue
                if 'function' not in row:
                    unowned += 1
                    continue
                functions.setdefault(row['function'], set()).add(row['address'])
        requests = [dict(address=address, sites=sorted(sites)) for address, sites in sorted(functions.items())]
        shards = min(self.cpu_limit * 4, max(1, math.ceil(len(requests) / 128)))
        budget = max(.015625, len(requests) * 65536 / GIB)
        p.update(indirect_functions_planned=len(requests), unowned_indirect_sites=unowned,
                 indirect_budget_gib=budget, indirect_jobs=[])
        for shard in range(shards if requests else 0):
            subset = requests[shard::shards]
            identifier = self.add_job(gap_job['program'], 'indirect', suffix=f'.{shard:04d}',
                source_dir=gap_job['source_dir'], source_name=gap_job['source_name'],
                snapshot_id=gap_job['snapshot_id'], functions=subset,
                output_limit_bytes=int(budget * GIB / shards),
                **{k: gap_job[k] for k in ('packed_file', 'packed_sha256', 'packed_cache') if k in gap_job})
            p['indirect_jobs'].append(identifier)
        self.queue_version += 1

    def finished(self):
        return (bool(self.programs) and all(p['state'] == 'complete' and p.get('gap_audit') for p in self.programs.values())
                and all(j['state'] == 'complete' for j in self.jobs.values()))

    def prepare_exports(self, p, result):
        program = dict(p['program'], analysis_audit=result['audit'])
        blockers = analysis_blockers(program)
        if blockers:
            raise RuntimeError(f'Analysis depth failed: {blockers}')
        audit = json.loads(Path(result['audit']).read_text())
        program['functions'] = audit['after']['functions']
        source = dict(self.manifest, project_dir=result['source_dir'], project_name=result['source_name'])
        if locks(source):
            raise RuntimeError('Finished analysis source is still locked')
        snapshot = hashlib.sha256(json.dumps(source_state(source), separators=(',', ':')).encode()).hexdigest()
        packed, dependencies = {}, []
        if result.get('packed_file'):
            if digest(result['packed_file']) != result.get('packed_sha256'):
                raise RuntimeError('Final packed database identity mismatch')
            packed = dict(packed_file=result['packed_file'], packed_sha256=result['packed_sha256'],
                          packed_cache=str(self.root / 'programs' / program['sha256'][:16] / 'packed-cache'))
            snapshot = packed['packed_sha256']
            dependencies = [self.add_job(program, 'warm', **packed)]
        # More shards than simultaneous workers let free slots steal work across binaries/tails.
        target = self.manifest.get('functions_per_shard', 1500)
        if not isinstance(target, int) or target < 1:
            raise ValueError('functions_per_shard must be a positive integer')
        shards = min(self.cpu_limit * 4, max(1, math.ceil(program['functions'] / target)))
        ids = []
        raw_budget = self.program_budget(p) * .4
        # Long qualified C++ names made the agent's symbols alone exceed the
        # old 512-bytes-per-function estimate. Reserve a quarter of raw output
        # for shard zero without increasing the program's total commitment.
        symbol_budget = raw_budget * .25
        for shard in range(shards):
            ids.append(self.add_job(program, 'export', dependencies, suffix=f'.{shard:04d}', shard=shard, shards=shards,
                                    source_dir=result['source_dir'], source_name=result['source_name'], snapshot_id=snapshot,
                                    output_limit_bytes=int(((raw_budget - symbol_budget) / shards +
                                                           (symbol_budget if shard == 0 else 0)) * GIB), **packed))
        self.add_job(program, 'index', ids)
        self.add_gaps(program, result['source_dir'], result['source_name'], packed=packed, deps=dependencies)
        p.update(state='exporting', audit=result['audit'], functions=program['functions'])
        self.queue_version += 1

    def external_updates(self):
        if time.monotonic() - self.external_checked_at < 5:
            return self.external_reserved_gib
        future_ram = 0
        for p in self.programs.values():
            if p['state'] != 'waiting_external':
                continue
            producer_unit = p['external_unit']
            producer_budget = 18
            alternative = self.adopt / 'parallel-analysis.json' if self.adopt else None
            if alternative and alternative.exists():
                pointer = json.loads(alternative.read_text())
                descriptor_path = Path(pointer['directory']) / 'complete-analysis.json'
                if descriptor_path.exists():
                    descriptor = json.loads(descriptor_path.read_text())
                    if (descriptor.get('sha256') == p['sha256'] and descriptor.get('complete')
                            and descriptor.get('constant_propagation_fixed_point')):
                        self.prepare_exports(p, descriptor)
                        p['parallel_analysis'] = str(descriptor_path)
                        # The validated, closed replacement is now adopted. Stop only our superseded run.
                        if p['external_unit'].startswith('dragonshard2-agent-ahead-'):
                            state = subprocess.check_output(['systemctl', '--user', 'show', p['external_unit'],
                                '-p', 'ActiveState', '--value'], text=True).strip()
                            if state in ('active', 'activating'):
                                subprocess.run(['systemctl', '--user', 'stop', p['external_unit']], check=True)
                        continue
                # After reboot the old serial fallback no longer exists. Follow the
                # verified replacement producer instead of failing on its stale unit.
                parallel_root = Path(pointer['directory'])
                owner_path, manifest_path = parallel_root / 'owner.json', parallel_root / 'manifest.json'
                if owner_path.exists() and manifest_path.exists():
                    owner = json.loads(owner_path.read_text())
                    manifest = json.loads(manifest_path.read_text())
                    if (owner.get('tool') == 'dragonshard-2-parallel-analysis' and
                            any(row['sha256'] == p['sha256'] for row in manifest['programs']) and
                            pointer.get('unit', '').startswith('dragonshard2-')):
                        producer_unit = pointer['unit']
                        producer_budget = 56
            directory = Path(p['external_dir'])
            if (directory / 'complete.json').exists():
                self.prepare_exports(p, {'audit': str(directory / 'depth.json'),
                                         'source_dir': str(directory), 'source_name': p['source_name']})
                continue
            result = subprocess.run(['systemctl', '--user', 'show', producer_unit,
                                     '-p', 'ActiveState', '-p', 'MemoryCurrent'], capture_output=True, text=True)
            props = dict(s.split('=', 1) for s in result.stdout.splitlines() if '=' in s)
            if props.get('ActiveState') not in ('active', 'activating'):
                raise RuntimeError(f'External analysis stopped without completed evidence: {p["name"]}')
            current = props.get('MemoryCurrent', '0')
            future_ram += max(0, producer_budget - (int(current) / GIB if current.isdigit() else 0))
        self.external_checked_at = time.monotonic()
        self.external_reserved_gib = future_ram
        return future_ram

    def sample_cpu(self):
        values = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
        current = sum(values), values[3] + values[4]
        if self.cpu_sample:
            delta = current[0] - self.cpu_sample[0]
            self.cpu_percent = 100 * (1 - (current[1] - self.cpu_sample[1]) / max(1, delta))
        self.cpu_sample = current

    def sage(self):
        if time.monotonic() - self.last_sage < 60:
            return
        probe = subprocess.run([self.manifest['sage_python'], str(HERE / 'sage_probe.py'),
                                '--command', self.manifest['sage_command']], capture_output=True,
                               text=True, timeout=45, check=True)
        atomic_json(self.root / 'systemsage.json', json.loads(probe.stdout))
        self.last_sage = time.monotonic()

    def program_budget(self, p):
        return 12 if p['program']['functions'] > 100000 else max(.2, p['program']['functions'] * 35000 / GIB)

    def output_reserve(self):
        # Reserve not-yet-written evidence, not the same bytes again after they are on disk.
        remaining = 0
        for p in self.programs.values():
            if 'indirect_jobs' in p:
                for identifier in p['indirect_jobs']:
                    job = self.jobs[identifier]
                    if job['state'] != 'complete':
                        # Partial writes already consumed disk; reserve only the remaining cap.
                        output = Path(job['work']) / 'indirect' / (identifier + '.jsonl')
                        partial = output.with_suffix('.jsonl.partial')
                        written = output.stat().st_size if output.exists() else partial.stat().st_size if partial.exists() else 0
                        remaining += max(0, job['output_limit_bytes'] - written) / GIB
            else:
                remaining += .25  # future data-flow evidence, adjusted to measured scope after audit
            if not p.get('gap_audit'):
                gap = self.jobs.get(p['sha256'][:16] + '.gaps', {})
                remaining += gap.get('output_limit_bytes', int(.125 * GIB)) / GIB
            if p['state'] == 'complete':
                continue
            budget = self.program_budget(p)
            work = self.root / 'programs' / p['sha256'][:16]
            written = sum(f.stat().st_size for f in (work / 'exports').glob('*') if f.is_file())
            written += sum(f.stat().st_size for f in work.glob('corpus.sqlite*') if f.is_file())
            remaining += max(0, budget - written / GIB)
        return remaining

    def run(self, *, planned=False):
        try:
            if not planned:
                self.plan()
            self.persist()
            while not self.stop:
                for identifier, process in list(self.active.items()):
                    if process.poll() is None:
                        continue
                    job = self.jobs[identifier]
                    del self.active[identifier]
                    if process.returncode:
                        job.update(state='failed', returncode=process.returncode)
                        self.event('task_failed', task=identifier, returncode=process.returncode)
                        self.queue_version += 1
                        # Independent binaries must keep advancing even if one task fails.
                        continue
                    result = json.loads((self.root / 'tasks' / (identifier + '.result.json')).read_text())
                    if result.get('task') != identifier or not result.get('complete'):
                        raise RuntimeError('Task result identity/completion mismatch')
                    job.update(state='complete', result=result, completed_at=time.time())
                    self.event('task_complete', task=identifier, kind=job['kind'])
                    self.queue_version += 1
                    p = self.programs[job['program']['sha256']]
                    if job['kind'] == 'analyze':
                        self.prepare_exports(p, result)
                    elif job['kind'] == 'index':
                        p.update(state='complete', database=result['database'], coverage=result['coverage'][0])
                    elif job['kind'] == 'gaps':
                        p['gap_audit'] = result
                        self.add_indirect(p, job, result)
                external_ram = self.external_updates()
                if self.finished():
                    self.persist('complete')
                    return
                self.sample_cpu()
                disk = shutil.disk_usage(self.root).free / GIB
                remaining_outputs = self.output_reserve()
                if disk < self.disk_reserve_gib + remaining_outputs:
                    raise RuntimeError('Space no longer covers unfinished output commitments; evidence retained')
                ready = ready_jobs(self.jobs)
                blocked = {}
                if ready:
                    self.sage()
                    # MemAvailable already excludes current RSS; reserve only worker growth here.
                    memory_by_group = process_group_memory(proc.pid for proc in self.active.values())
                    growth = sum(max(0, self.jobs[k]['memory_gib'] - memory_by_group.get(proc.pid, 0))
                                 for k, proc in self.active.items())
                    slots = self.cpu_limit - sum(self.jobs[k]['cpu'] for k in self.active)
                    selected, blocked = select_jobs(ready, cpu_slots=slots,
                        memory_gib=max(0, min(available() - 10 - external_ram,
                                              cgroup_memory_headroom()) - growth),
                        disk_gib=max(0, disk - self.disk_reserve_gib - remaining_outputs - sum(self.jobs[k]['disk_gib'] for k in self.active)))
                    for job in selected:
                        if job['kind'] == 'index':
                            work = Path(job['work'])
                            raw_bytes = sum(f.stat().st_size for f in (work / 'exports').glob('*') if f.is_file())
                            job['output_limit_bytes'] = int(self.program_budget(self.programs[job['program']['sha256']]) * GIB) - raw_bytes
                        path = self.root / 'tasks' / (job['id'] + '.json')
                        atomic_json(path, job)
                        with (self.root / 'logs' / (job['id'] + '.log')).open('w') as log:
                            proc = subprocess.Popen([sys.executable, str(HERE / 'swarm_tasks.py'), str(path)],
                                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                        self.active[job['id']] = proc
                        job.update(state='running', pid=proc.pid, started_at=time.time())
                        self.event('task_start', task=job['id'], kind=job['kind'], pid=proc.pid)
                        self.queue_version += 1
                if not ready and not self.active:
                    reason = 'waiting_for_external_analysis' if any(p['state'] == 'waiting_external' for p in self.programs.values()) else 'unsatisfied_dependencies'
                    if reason == 'unsatisfied_dependencies':
                        raise RuntimeError('All runnable work finished; failed tasks/dependencies remain, see queue.json and logs')
                elif ready and not self.active:
                    reason = 'resource_gate'
                else:
                    reason = None
                self.persist('running', reason=reason, blocked=blocked)
                time.sleep(2)
            raise RuntimeError('Coordinator stopped; completed evidence retained')
        except BaseException as exc:
            for proc in self.active.values():
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
            for proc in self.active.values():
                proc.wait()
            self.persist('blocked_or_failed', reason=str(exc))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--adopt-depth', type=Path)
    parser.add_argument('--cpu-slots', type=int)
    parser.add_argument('--adopt-queue', type=Path,
                        help='Reuse verified gap evidence from a prior idle queue with identical saved sources')
    parser.add_argument('--disk-reserve-gib', type=float, default=0,
                        help='Optional unused disk cushion; unfinished outputs are separately reserved')
    args = parser.parse_args()
    if args.cpu_slots is not None and args.cpu_slots < 1:
        parser.error('--cpu-slots must be positive')
    if not math.isfinite(args.disk_reserve_gib) or args.disk_reserve_gib < 0:
        parser.error('--disk-reserve-gib must be finite and nonnegative')
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / 'tasks').mkdir()
    (root / 'logs').mkdir()
    lock = (root / 'coordinator.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(args.manifest.read_text())
    atomic_json(root / 'manifest.json', manifest)
    atomic_json(root / 'owner.json', {'tool': 'dragonshard-2-swarm', 'pid': os.getpid()})
    swarm = Swarm(manifest, root, adopt=args.adopt_depth.resolve() if args.adopt_depth else None,
                  cpu_limit=args.cpu_slots, disk_reserve_gib=args.disk_reserve_gib,
                  adopt_queue=args.adopt_queue.resolve() if args.adopt_queue else None)
    def cancel(signum, frame):
        swarm.stop = True
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    swarm.run()


if __name__ == '__main__':
    main()
