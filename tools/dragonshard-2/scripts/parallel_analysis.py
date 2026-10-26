#!/usr/bin/env python3
"""Isolated JVMs compute constant-propagation shards, then verify a three-way program merge."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from dragonshard import HERE, GIB, analysis_blockers, atomic_json, digest, locks, source_state
from campaign import available
from preallocate_scratch import reserve_caches


def packing_slots(free, pending_output, temporary_bytes, limit):
    """Reserve unfinished files plus merge scratch, then spend remaining disk on saves."""
    return max(0, min(limit, (free - pending_output - 3 * temporary_bytes) // temporary_bytes))


def cgroup_packing_headroom(group, base):
    """Respect throttling as well as OOM limits, including inherited limits."""
    headroom = float('inf')
    while group != base:
        current = int((group / 'memory.current').read_text())
        for name in ('memory.high', 'memory.max'):
            value = (group / name).read_text().strip()
            if value.isdigit():
                headroom = min(headroom, max(0, int(value) - current - 2 * GIB))
        group = group.parent
    return headroom


def packing_memory_bytes():
    # NTFS dirty page cache is charged to the service too. Independent JVM heaps
    # fitting in RAM does not imply that all full-DB materializations fit at once.
    budget = max(0, (available() - 8) * GIB)
    relative = next((line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines()
                     if line.startswith('0::')), None)
    if relative:
        base = Path('/sys/fs/cgroup'); group = base / relative.lstrip('/')
        budget = min(budget, cgroup_packing_headroom(group, base))
    return int(budget)


def validate_worker(report, addresses, sha256, baseline_hash):
    assigned = report.get('assigned_functions', [])
    if (not report.get('complete') or report.get('sha256') != sha256 or
        report.get('baseline_sha256') != baseline_hash or
        report.get('attempted') != len(addresses) or len(assigned) != len(addresses) or
        set(assigned) != set(addresses)):
        raise RuntimeError('Worker identity/coverage mismatch; refusing adoption or merge')


def partition(functions, count):
    """Balance estimated instruction work; each function belongs to exactly one worker."""
    bins = [[] for _ in range(min(count, len(functions)))]
    weights = [0] * len(bins)
    for function in sorted(functions, key=lambda f: (-f['body_bytes'], f['address'])):
        target = min(range(len(bins)), key=lambda i: (weights[i], i))
        bins[target].append(function['address'])
        weights[target] += max(1, function['body_bytes'])
    return bins


def load_width_policy(path, sha256):
    """Only explicitly reviewed undefined-width cycles may retain untyped bytes."""
    if path is None:
        return []
    policy = json.loads(path.read_text())
    if policy.get('sha256') != sha256 or policy.get('schema') != 1:
        raise ValueError('Width policy identity/schema mismatch')
    ranges = policy.get('reviewed_untyped_widths')
    if not isinstance(ranges, list) or not ranges or len(ranges) > 64:
        raise ValueError('Width policy requires bounded reviewed ranges')
    for item in ranges:
        if not re.fullmatch(r'[0-9a-fA-F]{1,16}', item.get('address', '')):
            raise ValueError('Invalid width-policy address')
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}){1,4096}', item.get('hex', '')):
            raise ValueError('Invalid width-policy bytes')
        if not isinstance(item.get('reason'), str) or not item['reason'].strip():
            raise ValueError('Width policy requires the reviewed reason')
    return ranges


class Army:
    def __init__(self, manifest, program, root, workers, validate=False, strip_references=False,
                 checkpoint=None, checkpoint_only=False, finish_auto=False, pause_units=(), checkpoint_service=None, resume_workers=None, resume_merge=None, leave_paused_units=(), disk_reserve_gib=16, resume_round='round-00', width_policy=None):
        self.manifest, self.program, self.root = manifest, program, root
        self.workers, self.validate = workers, validate
        self.disk_reserve = int(disk_reserve_gib * GIB)
        self.strip_references = strip_references
        self.checkpoint, self.checkpoint_only = checkpoint, checkpoint_only
        self.finish_auto, self.pause_units = finish_auto, list(pause_units)
        self.checkpoint_service, self.frozen = checkpoint_service, []
        self.active, self.mutex, self.cancel = {}, threading.Lock(), threading.Event()
        self.resume_workers, self.resume_merge = resume_workers, resume_merge
        if not re.fullmatch(r'round-\d{2}', resume_round):
            raise ValueError('Invalid recovery round')
        self.resume_round = resume_round
        self.reviewed_widths = load_width_policy(width_policy, program['sha256'])
        if width_policy:
            atomic_json(root / 'width-policy.json', json.loads(width_policy.read_text()))
        self.leave_paused_units = set(leave_paused_units)
        self.pack_tasks, self.pack_baseline = [], None
        self.pack_updated = 0
        atomic_json(self.root / 'packing.json', dict(slots=1, phase='checkpoint'))
        self.status = dict(state='starting', binary=program['name'], requested_workers=workers,
                           complete=False, scope='Partitioned X86 constant propagation; not all Ghidra analyzers')

    def persist(self, **updates):
        self.status.update(updates)
        atomic_json(self.root / 'status.json', self.status)

    def sage(self):
        completed = subprocess.run([self.manifest['sage_python'], str(HERE / 'sage_probe.py'),
            '--command', self.manifest['sage_command']], capture_output=True, text=True, timeout=45, check=True)
        atomic_json(self.root / 'systemsage.json', json.loads(completed.stdout))

    def update_packing(self):
        with self.mutex:
            if not self.pack_tasks or time.monotonic() - self.pack_updated < 1:
                return
            self.pack_updated = time.monotonic()
            # Ghidra materializes an uncompressed DB before compression. Use actual cached
            # logical bytes, not compressed on-disk usage. Include measured growth allowance.
            raw = max((p.stat().st_size for p in (self.root / 'packed-cache').rglob('*.gbf')), default=0)
            temporary = (raw or self.pack_baseline.stat().st_size * 6) + 128 * 1024**2
            pending = 0
            for folder, cap in self.pack_tasks:
                finished, partial = folder / 'program.gzf', folder / 'program.gzf.partial'
                if not finished.exists():
                    try:
                        written = partial.stat().st_size
                    except FileNotFoundError:
                        written = cap if finished.exists() else 0
                    pending += max(0, cap - written)
            reserve_caches(self.root, self.disk_reserve + pending + 3 * temporary)
            free = shutil.disk_usage(self.root).free
            disk_slots = packing_slots(free - self.disk_reserve, pending, temporary, self.workers)
            memory_slots = packing_memory_bytes() // temporary
            slots = min(disk_slots, memory_slots)
            atomic_json(self.root / 'packing.json', dict(slots=slots, disk_permits=disk_slots, memory_permits=memory_slots, free_bytes=free,
                pending_output_bytes=pending, temporary_bytes_per_save=temporary,
                merge_scratch_bytes=3 * temporary, protected_disk_bytes=self.disk_reserve, phase='concurrent_saving'))

    def run_task(self, name, request, heap='640m'):
        work = self.root / name
        work.mkdir(parents=True, exist_ok=False)
        (work / 'driver').mkdir()
        (work / 'temp').mkdir()
        request = dict(request, output=str(work), sha256=self.program['sha256'], pack_lock=str(self.root / 'packing.lock'),
                       pack_control=str(self.root / 'packing.json'))
        atomic_json(work / 'request.json', request)
        # Shards each own one CPU slot. A solitary full-analyzer pass may use
        # the whole allocation; do not inherit the shard's one-CPU ceiling.
        cpu_limit = self.workers if request['mode'] in ('finish', 'checkpoint') else 1
        command = [str(Path(self.manifest['ghidra']) / 'support/analyzeHeadless'),
                   str(work / 'driver'), 'Driver', '-noanalysis', '-max-cpu', str(cpu_limit),
                   '-scriptPath', str(HERE), '-preScript', 'DragonShardAnalyze.java', str(work / 'request.json')]
        options = ' '.join([f'-Djava.io.tmpdir={work / "temp"}', f'-Dpdb.cache.dir={self.root / "packed-cache"}'])
        env = dict(os.environ, GHIDRA_HEADLESS_MAXMEM=heap, JAVA_TOOL_OPTIONS=options)
        with (work / 'headless.log').open('w') as log:
            with self.mutex:
                if self.cancel.is_set():
                    raise RuntimeError('Army cancelled')
                process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                self.active[name] = process
            try:
                while process.poll() is None:
                    if self.cancel.wait(1):
                        os.killpg(process.pid, signal.SIGTERM)
                        raise RuntimeError('Army cancelled')
                    self.update_packing()
                    # Exports begin AFTER disposable worker databases are reclaimed, so their
                    # future 12 GiB must not be double-reserved during this analysis stage.
                    if shutil.disk_usage(self.root).free < self.disk_reserve:
                        self.cancel.set()
                        os.killpg(process.pid, signal.SIGTERM)
                        raise RuntimeError('Configured disk-full protection reached; owned tasks stopped before overflow')
                    partial = work / 'program.gzf.partial'
                    if partial.exists() and partial.stat().st_size > request.get('max_output_bytes', 512 * 1024**2):
                        self.cancel.set()
                        os.killpg(process.pid, signal.SIGTERM)
                        raise RuntimeError('Task packed-output cap exceeded')
                if process.returncode:
                    raise RuntimeError(f'{name}: headless exit {process.returncode}; see {work / "headless.log"}')
                report = json.loads((work / 'report.json').read_text())
                if not report.get('complete') or report['sha256'] != self.program['sha256']:
                    raise RuntimeError(f'{name}: incomplete task: {report.get("error", "no completion marker")}')
                return work, report
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait()
                with self.mutex:
                    self.active.pop(name, None)

    def stop(self):
        self.cancel.set()

    def reclaim_workers(self, completed):
        """The verified merged program supersedes these task-owned disposable database replicas."""
        if self.active:
            raise RuntimeError('Cannot reclaim worker databases while tasks are running')
        for folder in completed:
            if (not folder.resolve().is_relative_to(self.root.resolve()) and
                not (self.resume_workers and folder.resolve().is_relative_to((self.resume_workers / self.resume_round / 'workers').resolve()))) or folder.is_symlink():
                raise RuntimeError('Unowned worker database path')
            (folder / 'program.gzf').unlink()
        # Cache only contains unpackings of immutable files, never the sole research output.
        cache = self.root / 'packed-cache'
        if cache.exists():
            if cache.is_symlink():
                raise RuntimeError('Unexpected cache symlink')
            shutil.rmtree(cache)

    def publish_finished(self, finish_report, completed, iteration):
        descriptor = dict(sha256=self.program['sha256'], complete=True,
            source_dir=finish_report['source_dir'], source_name=finish_report['source_name'],
            audit=finish_report['audit'], constant_propagation_fixed_point=True)
        if self.reviewed_widths:
            descriptor['reviewed_undefined_width_policy'] = str(self.root / 'width-policy.json')
        if analysis_blockers(dict(self.program, analysis_audit=descriptor['audit'])):
            raise RuntimeError('Final parallel analysis failed listing-depth validation')
        if locks(dict(project_dir=descriptor['source_dir'], project_name=descriptor['source_name'])):
            raise RuntimeError('Final analysis project is not closed')
        self.reclaim_workers(completed)
        atomic_json(self.root / 'complete-analysis.json', descriptor)
        self.persist(state='complete', complete=True, full_auto_analysis_complete=True,
                     result=descriptor, convergence_rounds=iteration)

    def analysis_round(self, checkpoint, prefix):
        baseline = checkpoint / 'program.gzf'
        baseline_hash = digest(baseline)
        functions = json.loads((checkpoint / 'functions.json').read_text())
        shards = partition(functions, self.workers)
        if not shards:
            raise RuntimeError('No native functions to analyze')
        reused = self.resume_workers if prefix == 'round-00' else None
        paths = [(reused / self.resume_round / 'workers' / f'{i:03d}') if reused else
                 (self.root / prefix / 'workers' / f'{i:03d}') for i in range(len(shards))]
        if reused:
            owner = json.loads((reused / 'owner.json').read_text())
            prior = json.loads((reused / 'status.json').read_text())
            if owner.get('tool') != 'dragonshard-2-parallel-analysis' or prior.get('state') != 'blocked_or_failed':
                raise RuntimeError('Recovery requires a stopped, failed, owned analysis run')
            for path, addresses in zip(paths, shards):
                report = json.loads((path / 'report.json').read_text())
                validate_worker(report, addresses, self.program['sha256'], baseline_hash)
                if not (path / 'program.gzf').is_file():
                    raise RuntimeError('Saved worker database missing')
            atomic_json(self.root / 'reused-workers.json', dict(source=str(reused), workers=[str(p) for p in paths],
                                                             baseline_sha256=baseline_hash))
        # Keep the partition stable for recovery; run every ready shard up to CPU/RAM
        # admission capacity, and refill vacated slots immediately without a cohort barrier.
        if reused and self.resume_merge:
            result = json.loads((self.resume_merge / 'report.json').read_text())
            expected = {str(p.resolve()) for p in paths}
            got = [str(Path(step['worker']).resolve()) for step in result.get('steps', [])]
            if (not result.get('complete') or result.get('sha256') != self.program['sha256'] or
                result.get('baseline_sha256') != baseline_hash or len(got) != len(expected) or
                result.get('reviewed_untyped_widths', []) != self.reviewed_widths or
                set(got) != expected or not (self.resume_merge / 'program.gzf').is_file()):
                raise RuntimeError('Saved merge is not a complete verified merge of these workers')
            self.persist(state='adopted_saved_merge', saved_merge=str(self.resume_merge),
                         functions=len(functions), completed_shards=len(shards), shards=len(shards))
            return self.resume_merge, result, paths
        mana = available()
        concurrent = 0 if reused else min(len(shards), max(0, int((mana - 8) / 1.05)), max(1, (os.cpu_count() or 1) - 1))
        if mana < 8 or (not reused and concurrent < 1):
            raise RuntimeError(f'Insufficient RAM for merge plus a worker: {mana:.1f} GiB available')
        cap = baseline.stat().st_size + 32 * 1024**2
        if not reused and shutil.disk_usage(self.root).free < len(shards) * cap + 4 * baseline.stat().st_size + self.disk_reserve:
            raise RuntimeError('Worker output commitments and merge scratch exceed available disk')
        self.pack_tasks = [] if reused else list(zip(paths, [cap] * len(paths)))
        self.pack_baseline = baseline
        self.pack_updated = 0
        self.update_packing()
        self.sage()
        self.persist(state='recovering_saved_workers' if reused else 'analysis_save_merge_pipeline',
                     round=prefix, functions=len(functions), shards=len(shards), admitted_workers=concurrent,
                     completed_shards=len(shards) if reused else 0, worker_heap='640m',
                     baseline=str(baseline), baseline_sha256=baseline_hash)
        merge_base = self.root / prefix / 'merge-base.gzf'
        merge_base.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['cp', '--reflink=auto', '--', str(baseline), str(merge_base)], check=True)
        comparison_base = self.root / prefix / 'comparison-base.gzf'
        subprocess.run(['cp', '--reflink=auto', '--', str(baseline), str(comparison_base)], check=True)
        self.persist(state='warming_immutable_database_cache')
        self.run_task(prefix + '/warm-cache', dict(mode='warm_cache',
            cache_paths=[str(baseline), str(comparison_base)]))
        self.persist(state='recovering_saved_workers' if reused else 'analysis_save_merge_pipeline')
        request = dict(mode='merge', baseline=str(baseline), baseline_sha256=baseline_hash,
                       merge_base=str(merge_base), workers=[str(p) for p in paths],
                       retain_access_widths=True, max_output_bytes=2 * GIB)
        if self.reviewed_widths:
            request['reviewed_untyped_widths'] = self.reviewed_widths
        completed = list(paths) if reused else []
        # The verifier is a consumer of saved databases, not a barrier after all producers.
        # A merge error is collected after producers exit, preserving completed computation.
        with ThreadPoolExecutor(max_workers=concurrent + 1) as pool:
            merging = pool.submit(self.run_task, prefix + '/merge', request, '6G')
            futures = {} if reused else {pool.submit(self.run_task, f'{prefix}/workers/{i:03d}',
                dict(mode='worker', baseline=str(baseline), baseline_sha256=baseline_hash,
                     comparison_baseline=str(comparison_base), functions=addresses, max_output_bytes=cap)): i for i, addresses in enumerate(shards)}
            try:
                for future in as_completed(futures):
                    work, result = future.result()
                    validate_worker(result, shards[futures[future]], self.program['sha256'], baseline_hash)
                    completed.append(work)
                    self.persist(completed_shards=len(completed))
                if digest(baseline) != baseline_hash:
                    raise RuntimeError('Immutable checkpoint was modified')
                self.persist(state='verifying_merge')
                merged, merge_report = merging.result()
            except BaseException:
                self.stop()
                raise
        self.pack_tasks = []
        atomic_json(self.root / 'packing.json', dict(slots=1, phase='single_result'))
        return merged, merge_report, completed

    def run(self):
        try:
            self.sage()
            if digest(self.program['binary_path']) != self.program['sha256']:
                raise RuntimeError('Selected executable changed')
            if locks(self.manifest):
                raise RuntimeError('Checkpoint source must be closed and saved')
            if self.checkpoint:
                checkpoint = self.checkpoint
                if self.checkpoint_service:
                    self.persist(state='waiting_for_checkpoint')
                    while True:
                        parent_state = json.loads((checkpoint.parent / 'status.json').read_text())
                        if parent_state['state'] == 'checkpoint_ready':
                            break
                        if parent_state['state'] == 'blocked_or_failed':
                            raise RuntimeError('Checkpoint producer failed: ' + parent_state.get('error', 'unknown'))
                        state = subprocess.check_output(['systemctl', '--user', 'show', self.checkpoint_service,
                                                         '-p', 'ActiveState', '--value'], text=True).strip()
                        if state not in ('active', 'activating'):
                            raise RuntimeError('Checkpoint producer stopped without publishing a checkpoint')
                        if self.cancel.wait(3):
                            raise RuntimeError('Cancelled while awaiting checkpoint')
                report = json.loads((checkpoint / 'report.json').read_text())
                if not report.get('complete') or report.get('sha256') != self.program['sha256']:
                    raise RuntimeError('Supplied checkpoint is incomplete or for another executable')
            else:
                before = source_state(self.manifest)
                self.persist(state='checkpointing')
                checkpoint, report = self.run_task('checkpoint', dict(mode='checkpoint',
                    source_dir=self.manifest['project_dir'], source_name=self.manifest['project_name'],
                    program_path=self.program['project_path'], max_output_bytes=2 * GIB,
                    validation_strip_references=self.strip_references), heap='6G')
                if before != source_state(self.manifest):
                    raise RuntimeError('Original source changed during checkpoint')
            if self.checkpoint_only:
                self.persist(state='checkpoint_ready', checkpoint=str(checkpoint))
                return
            for unit in self.pause_units:
                if not unit.startswith('dragonshard2-'):
                    raise RuntimeError('Only explicitly selected DragonShard services may be frozen')
                subprocess.run(['systemctl', '--user', 'freeze', unit], check=True)
                self.frozen.append(unit)
            merged, merge_report, completed = self.analysis_round(checkpoint, 'round-00')
            baseline = checkpoint / 'program.gzf'
            baseline_hash = digest(baseline)
            functions = json.loads((checkpoint / 'functions.json').read_text())
            if self.validate:
                self.persist(state='validating_against_serial')
                serial, _ = self.run_task('serial', dict(mode='worker', baseline=str(baseline),
                    baseline_sha256=baseline_hash, functions=[f['address'] for f in functions],
                    max_output_bytes=2 * GIB), heap='2G')
                _, comparison = self.run_task('comparison', dict(mode='compare', one=str(merged / 'program.gzf'),
                    two=str(serial / 'program.gzf'), max_output_bytes=GIB), heap='2G')
                self.status['serial_equivalent'] = comparison['equal']
                if self.strip_references and merge_report['changed_address_bytes'] == 0:
                    raise RuntimeError('Validation fixture produced no real analysis changes')
            if self.finish_auto:
                # A post-reboot finish checkpoint already includes every remaining
                # analyzer. A zero-change reconciliation proves its fixed point;
                # do not repeat that expensive serial stage and another army.
                if report.get('mode') == 'finish' and merge_report['changed_address_bytes'] == 0:
                    self.publish_finished(report, completed, 0)
                    return
                for iteration in range(1, 5):
                    self.reclaim_workers(completed)
                    self.persist(state='remaining_analyzers', round=iteration)
                    finished, finish_report = self.run_task(f'finish-{iteration:02d}', dict(mode='finish',
                        baseline=str(merged / 'program.gzf'), program_path=self.program['project_path'],
                        max_output_bytes=2 * GIB), heap='8G')
                    merged, merge_report, completed = self.analysis_round(finished, f'round-{iteration:02d}')
                    if merge_report['changed_address_bytes'] == 0:
                        self.publish_finished(finish_report, completed, iteration)
                        return
                raise RuntimeError('Analysis has not converged after four reconciliation rounds; reference run retained')
            self.persist(state='complete', complete=True, result=str(merged / 'program.gzf'),
                         changed_address_bytes=merge_report['changed_address_bytes'],
                         full_auto_analysis_complete=False)
        except BaseException as exception:
            self.stop()
            self.persist(state='blocked_or_failed', error=str(exception))
            raise
        finally:
            for unit in reversed(self.frozen):
                if unit in self.leave_paused_units:
                    continue
                subprocess.run(['systemctl', '--user', 'thaw', unit], check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--workers', type=int, default=42)
    parser.add_argument('--disk-reserve-gib', type=float, default=16,
                        help='Unused space floor; use at least the external guard threshold on this filesystem')
    parser.add_argument('--validate-serial', action='store_true')
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--checkpoint-only', action='store_true')
    parser.add_argument('--resume-merge', type=Path, help='Adopt a complete verified merge without replaying it')
    parser.add_argument('--resume-workers', type=Path, help='Reuse all verified worker databases from a failed analysis run')
    parser.add_argument('--resume-round', default='round-00', help='Exact saved round within --resume-workers')
    parser.add_argument('--width-policy', type=Path, help='Byte-verified reviewed undefined-width cycles; alternatives remain in merge evidence')
    parser.add_argument('--checkpoint-service')
    parser.add_argument('--finish-auto-analysis', action='store_true')
    parser.add_argument('--pause-unit', action='append', default=[])
    parser.add_argument('--leave-paused-unit', action='append', default=[], help='Preserve a retained live fallback without resuming writes on its old drive')
    parser.add_argument('--validation-strip-references', action='store_true',
                        help='Fixture-only: remove derived references from the private checkpoint to exercise nontrivial changes')
    args = parser.parse_args()
    if not math.isfinite(args.disk_reserve_gib) or args.disk_reserve_gib < 0:
        parser.error('--disk-reserve-gib must be finite and nonnegative')
    if not set(args.leave_paused_unit).issubset(args.pause_unit):
        parser.error('--leave-paused-unit must also be an explicit --pause-unit')
    if args.resume_merge and not args.resume_workers:
        parser.error('--resume-merge requires --resume-workers for verified coverage and ownership')
    if not re.fullmatch(r'round-\d{2}', args.resume_round) or (args.resume_round != 'round-00' and not args.resume_workers):
        parser.error('--resume-round requires a valid round-NN and --resume-workers')
    if args.validation_strip_references and not args.validate_serial:
        parser.error('--validation-strip-references requires --validate-serial')
    if args.workers < 1 or args.workers > (os.cpu_count() or 1):
        parser.error('workers must be between 1 and the machine CPU count')
    manifest = json.loads(args.manifest.read_text())
    selected = [p for p in manifest['programs'] if not args.sha256 or p['sha256'] == args.sha256]
    if len(selected) != 1:
        parser.error('select exactly one binary with --sha256')
    root = args.directory.resolve()
    if ' ' in str(root):
        parser.error('choose an output path without spaces for JVM option transport')
    root.mkdir(parents=True, exist_ok=False)
    atomic_json(root / 'owner.json', {'tool': 'dragonshard-2-parallel-analysis', 'pid': os.getpid()})
    atomic_json(root / 'manifest.json', manifest)
    army = Army(manifest, selected[0], root, args.workers, args.validate_serial, args.validation_strip_references,
                args.checkpoint.resolve() if args.checkpoint else None, args.checkpoint_only,
                args.finish_auto_analysis, args.pause_unit, args.checkpoint_service,
                args.resume_workers.resolve() if args.resume_workers else None,
                args.resume_merge.resolve() if args.resume_merge else None, args.leave_paused_unit,
                args.disk_reserve_gib, args.resume_round, args.width_policy)
    signal.signal(signal.SIGTERM, lambda *_: army.stop())
    signal.signal(signal.SIGINT, lambda *_: army.stop())
    army.run()


if __name__ == '__main__':
    main()
