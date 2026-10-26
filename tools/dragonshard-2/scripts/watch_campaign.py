#!/usr/bin/env python3
"""Read-only terminal scrying glass for DragonShard campaign files and owned workers."""
import argparse
import curses
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def tail(path, limit=65536):
    try:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - limit))
            return stream.read(limit)
    except OSError:
        return b''


def shard_progress(path, previous=0):
    data = tail(path)
    # An interrupted JSON line is not yet a persisted function record.
    data = data[:data.rfind(b'\n') + 1]
    completed = re.search(rb'\{"kind":"complete"[^\n]*', data)
    if completed:
        try:
            return json.loads(completed.group())['attempted'], True
        except (ValueError, KeyError):
            pass
    try:
        with path.open('rb') as stream:
            header = json.loads(stream.readline())
        ordinals = re.findall(rb'(?:^|\n)\{"kind":"function","ordinal":(\d+)', data)
        if ordinals:
            first = header.get('shard', 0)
            if header.get('segment'):
                first += max(0, (header['ordinal_start'] - first + header['shards'] - 1) // header['shards']) * header['shards']
            previous = max(previous, (int(ordinals[-1]) - first) // header['shards'] + 1)
    except (OSError, ValueError, KeyError):
        pass
    return previous, False


class Watcher:
    def __init__(self, root):
        self.root = root
        self.ticks = os.sysconf('SC_CLK_TCK')
        self.cpu_previous = None
        self.process_previous = {}
        self.shards = {}
        self.gpu = 'GPU: no Ghidra compute backend'
        self.gpu_time = 0
        self.queue_cache = {}
        self.queue_mtime = None
        self.evidence_samples = []
        self.final_samples = {}

    def sample(self):
        now = time.monotonic()
        status = read_json(self.root / 'status.json')
        pointer = read_json(self.root / 'swarm.json')
        swarm_root = Path(pointer['directory']) if pointer.get('directory') else None
        swarm_status = read_json(swarm_root / 'status.json') if swarm_root else {}
        if swarm_status:
            queue_path = swarm_root / 'queue.json'
            try:
                mtime = queue_path.stat().st_mtime_ns
                if mtime != self.queue_mtime:
                    self.queue_cache = read_json(queue_path)
                    self.queue_mtime = mtime
            except OSError:
                pass
            status = dict(status, state='swarm: ' + swarm_status['state'])
        totals = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
        total, idle, iowait = sum(totals), totals[3] + totals[4], totals[4]
        cpu = io_wait = None
        if self.cpu_previous:
            old_total, old_idle, old_iowait = self.cpu_previous
            cpu = 100 * (1 - (idle - old_idle) / max(1, total - old_total))
            io_wait = 100 * max(0, iowait - old_iowait) / max(1, total - old_total)
        self.cpu_previous = total, idle, iowait
        cpu_text = 'sampling' if cpu is None else f'{cpu:5.1f}%'
        wait_text = 'sampling' if io_wait is None else f'{io_wait:.1f}%'
        memory = {x.split(':')[0]: int(x.split()[1]) / 1024**2 for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith(('MemTotal:', 'MemAvailable:'))}
        lines = ['DRAGONSHARD-2  |  SCRYING GLASS',
                 f"Phase: {status.get('state', 'starting')}   |   q / Ctrl-C closes ONLY this watcher",
                 f"Host CPU busy {cpu_text}; I/O wait {wait_text} / {os.cpu_count()} logical CPUs   Mana available {memory['MemAvailable']:.1f} / {memory['MemTotal']:.1f} GiB",
                 f"Working disk available {shutil.disk_usage(swarm_root or self.root).free / 1024**3:.1f} GiB   |   {swarm_status.get('disk_reserve_gib', 8)} GiB unused cushion; unfinished outputs reserved"]
        if now - self.gpu_time > 10:
            try:
                result = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    self.gpu = 'GPU (whole system): ' + result.stdout.strip() + ' (% / MiB); Ghidra uses CPU'
            except (OSError, subprocess.TimeoutExpired):
                pass
            self.gpu_time = now
        lines.append(self.gpu)
        guard = read_json(Path('/mnt/Captures/DragonShard-2/disk-guard.json'))
        if guard:
            age = max(0, time.time() - guard.get('at', 0))
            guard_state = guard.get('state', '?') if age < 15 else 'STALE - guard not reporting'
            checks = '; '.join(f"{r['path']}: {r['free_bytes'] / 1024**3:.1f} GiB free / {r['minimum_free_bytes'] / 1024**3:.0f} minimum"
                               for r in guard.get('checks', []))
            lines.append(f'Disk guard: {guard_state} | {checks}')
        coverage = [p for b in status.get('batches', []) for p in b['coverage']]
        if swarm_status:
            coverage = [p['coverage'] for p in self.queue_cache.get('programs', []) if p.get('coverage')]
        functions = sum(p['recognized_internal_functions'] for p in coverage)
        success = sum(p['outcomes'].get('ok', 0) for p in coverage)
        lines += ['', f"Indexed programs: {len(coverage)}/{status.get('scope_programs', 21)}   Functions: {functions:,}   C successful: {success:,}"]
        if status.get('reason'):
            lines.append('STOP REASON: ' + status['reason'])
        if swarm_status:
            lines.append(f"Global queue: {swarm_status['active_tasks']} active / {swarm_status['ready_tasks']} ready / CPU ceiling {swarm_status['cpu_slots_limit']}")
            lines.append(f"Detailed gap audits: {swarm_status.get('programs_gap_audited', 0)}/{swarm_status['programs_total']}")
            lines.append(f"Indirect data-flow shards: {swarm_status.get('indirect_shards_complete', 0)}/{swarm_status.get('indirect_shards_total', 0)}")
            if swarm_status.get('reason'):
                lines.append('Queue wait: ' + swarm_status['reason'])
            if swarm_status.get('idle_reasons'):
                lines.append('Capacity gates: ' + str(swarm_status['idle_reasons']))
            for p in self.queue_cache.get('programs', []):
                if p.get('parallel_analysis') or p['state'] not in ('complete', 'waiting_external'):
                    jobs = [j for j in self.queue_cache.get('jobs', {}).values() if j['program']['sha256'] == p['sha256'] and j['kind'] == 'export']
                    done = sum(j['state'] == 'complete' for j in jobs)
                    if jobs:
                        lines.append(f"{p['name']}: {done}/{len(jobs)} export shards complete")
                        if p.get('parallel_analysis'):
                            observed = {}
                            for path in (Path(jobs[0]['work']) / 'exports').glob('*.jsonl*'):
                                key = str(path).removesuffix('.partial')
                                count, _ = shard_progress(path, self.shards.get(key, 0))
                                self.shards[key] = count
                                observed[key] = max(observed.get(key, 0), count)
                                if path.name.startswith('0000-') and path.suffix == '.partial' and count == 0:
                                    try:
                                        lines.append(f"FINAL SYMBOL EXPORT: {path.stat().st_size / 1024**2:,.1f} MiB written; function counter advances after symbols")
                                    except OSError:
                                        pass  # The worker may have atomically finalized this file.
                            written = sum(observed.values())
                            expected = p.get('functions', 0)
                            percent = 100 * written / expected if expected else 0
                            samples = self.final_samples.setdefault(p['sha256'], [])
                            if samples and written < samples[-1][1]:
                                samples.clear()
                            samples.append((now, written))
                            samples[:] = [s for s in samples if now - s[0] <= 60]
                            elapsed = now - samples[0][0]
                            rate = f"{(written - samples[0][1]) / elapsed:,.1f} functions/s" if elapsed else 'sampling'
                            lines.append(f"FINAL ANALYSIS EXPORT: {written:,}/{expected:,} agent functions ({percent:.2f}%); index {'complete' if p['state'] == 'complete' else 'pending'}")
                            filled = min(50, int(percent / 2))
                            lines.append('FINAL EXPORT RATE: ' + rate + ' (recent samples)  [' + '#' * filled + '-' * (50 - filled) + ']')
        parallel_pointer = read_json(self.root / 'parallel-analysis.json')
        final_analysis = {}
        if parallel_pointer:
            parallel_root = Path(parallel_pointer['directory'])
            parallel = read_json(parallel_root / 'status.json')
            final_analysis = read_json(parallel_root / 'complete-analysis.json')
            if final_analysis.get('complete') and final_analysis.get('constant_propagation_fixed_point'):
                lines.append('FINAL ANALYSIS GATE: COMPLETE | remaining analyzers and reconciled constant propagation verified')
            else:
                lines.append(f"INDEPENDENT ANALYSIS: {parallel.get('state', 'starting')} | worker ceiling {parallel.get('requested_workers', '?')}")
            if parallel_pointer.get('convergence_recovery'):
                recovery = read_json(Path(parallel_pointer['convergence_recovery']))
                lines.append(f"Reviewed width-cycle recovery: {recovery.get('state', 'starting')}; saved worker evidence will be reused")
            pack = read_json(parallel_root / 'packing.json')
            if parallel.get('state') == 'remaining_analyzers':
                lines.append(f"Partitioned pass saved {parallel.get('completed_shards', 0)}/{parallel.get('shards', '?')} shards; remaining shared-database analyzers running")
            elif not final_analysis.get('complete'):
                lines.append(f"Worker target {parallel.get('requested_workers', '?')}; admitted analysis workers {parallel.get('admitted_workers', '?')}; current save permits {pack.get('slots', '?')}")
            if pack.get('pending_output_bytes') is not None:
                lines.append(f"Save disk commitments: unfinished outputs {pack['pending_output_bytes'] / 1024**3:.1f} GiB; temporary DB per save {pack['temporary_bytes_per_save'] / 1024**3:.1f} GiB")
            round_name = parallel.get('round')
            if isinstance(round_name, str):
                reports = [read_json(path) for path in (parallel_root / round_name / 'workers').glob('*/report.json')]
                reuse = read_json(parallel_root / 'reused-workers.json')
                if not reports and round_name == 'round-00' and reuse:
                    reports = [read_json(Path(p) / 'report.json') for p in reuse['workers']]
                phases = Counter('saved' if r.get('complete') else r.get('phase', 'computing') for r in reports)
                lines.append('Worker stages: ' + ', '.join(f'{name}={count}' for name, count in sorted(phases.items())))
                merge = read_json(parallel_root / round_name / 'merge/report.json')
                if merge:
                    lines.append(f"Merge verification: {len(merge.get('steps', []))}/{parallel.get('shards', '?')} inputs; {merge.get('phase', 'opening databases')}")
                attempted = sum(report.get('attempted', 0) for report in reports)
                lines.append(f"Constant propagation {round_name}: {attempted:,}/{parallel.get('functions', 0):,} functions; {parallel.get('completed_shards', 0)}/{parallel.get('shards', 0)} shards")
                if parallel.get('functions', 0) and attempted >= parallel['functions'] and parallel.get('state') in ('parallel_constant_propagation', 'analysis_save_merge_pipeline'):
                    lines.append('Function computation complete; saving and merge verification continue')
            if parallel.get('error'):
                lines.append('Parallel analysis stopped: ' + parallel['error'])
        checkpoint_pointer = read_json(self.root / 'checkpoint-export.json')
        if checkpoint_pointer:
            checkpoint_root = Path(checkpoint_pointer['directory'])
            checkpoint = read_json(checkpoint_root / 'status.json')
            seen = done = 0
            complete_shards = set()
            for path in sorted((checkpoint_root / 'exports').glob('*.jsonl*')):
                shard_number = int(path.name.split('.')[0])
                if shard_number in complete_shards:
                    continue
                key = str(path).removesuffix('.partial')
                count, complete = shard_progress(path, self.shards.get(key, 0))
                self.shards[key] = count
                seen += count
                done += complete
                if complete:
                    complete_shards.add(shard_number)
            for path in (checkpoint_root / 'segments').glob('*.jsonl*'):
                try:
                    with path.open('rb') as stream:
                        header = json.loads(stream.readline())
                    # Once assembled, the whole shard already includes these records.
                    if header['shard'] in complete_shards:
                        continue
                    key = str(path).removesuffix('.partial')
                    count, _ = shard_progress(path, self.shards.get(key, 0))
                    self.shards[key] = count
                    seen += count
                except (OSError, ValueError, KeyError):
                    pass
            denominator = checkpoint.get('functions', 0)
            seen = min(denominator, max(seen, checkpoint.get('exported_functions', 0)))
            percent = 100 * seen / denominator if denominator else 0
            checkpoint_manifest = read_json(checkpoint_root / 'manifest.json')
            selected = checkpoint_manifest.get('programs', [{}])[0].get('sha256')
            known = {p['sha256']: p.get('coverage', {}).get('recognized_internal_functions',
                     p.get('program', {}).get('functions', 0)) for p in self.queue_cache.get('programs', [])}
            indexed = {p['sha256']: p['recognized_internal_functions'] for p in coverage}
            if selected:
                known[selected] = max(known.get(selected, 0), denominator)
                indexed[selected] = max(indexed.get(selected, 0), seen)
            total_known, total_written = sum(known.values()), sum(indexed.values())
            if total_known:
                overall = 100 * total_written / total_known
                self.evidence_samples.append((now, total_written))
                self.evidence_samples = [row for row in self.evidence_samples if now - row[0] <= 60]
                oldest = self.evidence_samples[0]
                rate = (total_written - oldest[1]) / max(.001, now - oldest[0])
                filled = min(40, int(overall * .4))
                lines[2:2] = [
                    f'DEEP FUNCTION EVIDENCE: {overall:6.2f}% | {total_written:,} / {total_known:,} currently identified functions',
                    '[' + '#' * filled + '-' * (40 - filled) + f']  Target 99% | +{rate:,.0f} functions/s (recent samples)',
                    'Includes earlier checkpoint exports. The FINAL ANALYSIS EXPORT counter below tracks the converged snapshot.']
            lines.append(f"SAVED CHECKPOINT EXPORT: {checkpoint.get('state', 'starting')} | {checkpoint.get('active_workers', 0)} JVMs | {done}/{checkpoint.get('requested_workers', '?')} shards")
            if checkpoint.get('recovery_segments'):
                lines.append(f"Missing-function recovery: {checkpoint.get('completed_segments', 0)}/{checkpoint['recovery_segments']} segments saved; {checkpoint.get('admitted_workers', 0)} worker slots")
            lines.append(f"Earlier checkpoint evidence: {seen:,}/{denominator:,} functions ({percent:.2f}%); distinct from the final export/index.")
            if checkpoint.get('error'):
                lines.append('Checkpoint export stopped: ' + checkpoint['error'])
        indirect_pointer = read_json(self.root / 'checkpoint-indirect.json')
        if indirect_pointer:
            indirect = read_json(Path(indirect_pointer['directory']) / 'status.json')
            lines.append(f"Checkpoint indirect-call tracing: {indirect.get('state', 'starting')} | {indirect.get('completed_shards', 0)}/{indirect.get('workers', '?')} shards saved")
        enrichment_pointer = read_json(self.root / 'enrichment.json')
        if enrichment_pointer:
            enrichment_root = Path(enrichment_pointer['directory'])
            enrichment = read_json(enrichment_root / 'status.json')
            lines.append(f"ANALYSIS ENRICHMENT: {enrichment.get('state', 'starting')} | {enrichment.get('active', 0)}/{enrichment.get('workers', '?')} active workers | {enrichment.get('ready', 0)} ready")
            lines.append(f"Enrichment jobs: {enrichment.get('completed', 0)}/{enrichment.get('jobs', '?')} saved; {enrichment.get('failed', 0)} failures | C retries recovered: {enrichment.get('exception_recoveries', 0)}")
            count = enrichment.get('functions_fid_scanned', 0)
            for p in enrichment_root.glob('tasks/*/evidence.jsonl.partial'):
                if '.fid.' not in p.parent.name:
                    continue
                key = str(p)
                progress, _ = shard_progress(p, self.shards.get(key, 0))
                self.shards[key] = progress
                count += progress
            lines.append(f"Library fingerprint coverage: at least {count:,} functions; {enrichment.get('function_matches', 0):,} completed-shard candidate matches (not proven identities)")
        static_pointer = read_json(self.root / 'static-evidence.json')
        if static_pointer:
            static = read_json(Path(static_pointer['directory']) / 'status.json')
            totals = static.get('totals', {})
            lines.append(f"STATIC EVIDENCE: {static.get('completed', 0)}/{static.get('jobs', '?')} jobs | {totals.get('tables_with_structural_rtti', 0):,} RTTI-associated tables; {totals.get('dispatch_slot_candidates', 0):,} dispatch offsets")
        symbols_pointer = read_json(self.root / 'symbol-overlay.json')
        if symbols_pointer:
            symbols = read_json(Path(symbols_pointer['directory']) / 'status.json')
            lines.append(f"PUBLIC SYMBOLS: {symbols.get('state', 'starting')} | {symbols.get('matching_pdbs', 0)}/{symbols.get('programs', '?')} matching PDBs; {symbols.get('public_symbols', 0):,} symbols; {symbols.get('anonymous_functions_named', 0):,} anonymous functions named")
        retry_pointer = read_json(self.root / 'exception-reconciliation.json')
        if retry_pointer:
            retry = read_json(Path(retry_pointer['status']))
            lines.append(f"EXCEPTION RECONCILIATION: {retry.get('state', 'starting')} | {retry.get('standalone_c_recoveries', 0)} standalone C recoveries; {retry.get('fragment_entries_recovered_in_parents', 0)} fragment entries recovered in {retry.get('parent_functions_recovered', 0)} parent functions; original failure records retained")
        for batch in sorted(self.root.glob('batch[0-9]*')):
            plan = read_json(batch / 'run/jobs.json', [])
            if not plan:
                continue
            seen = done = 0
            for p in (batch / 'run/exports').glob('*.jsonl*'):
                key = str(p).removesuffix('.partial')
                count, complete = shard_progress(p, self.shards.get(key, 0))
                self.shards[key] = count
                seen += count
                done += complete
            lines.append(f'{batch.name}: {done}/{len(plan)} complete shards; at least {seen:,} function records written')
        reports = [self.root / 'coremessagingxp-pilot/depth.json', *self.root.glob('batch*/analysis/*/depth.json'), *self.root.glob('ahead/*/depth.json')]
        depths = {j['sha256']: j for p in reports if (j := read_json(p)).get('sha256')}
        if final_analysis.get('complete') and final_analysis.get('constant_propagation_fixed_point'):
            audit = read_json(Path(final_analysis['audit']))
            if audit.get('complete') and audit.get('sha256') == final_analysis['sha256']:
                depths[audit['sha256']] = audit
        finished = [j for j in depths.values() if j.get('complete')]
        lines.append(f'Deeper analysis completed: {len(finished)}/21 programs (file count, not total work %)')
        for report in depths.values():
            if not report.get('complete'):
                lines.append(f"Analyzing: {report['program']} | {report.get('phase', 'disassembly / body repair')}")
        exceptions = read_json(self.root / 'exception-evidence/catalog.json')
        if exceptions.get('complete'):
            lines.append(f"Supplemental assembly / p-code saved for {len(exceptions['functions'])} failed functions")
        lines += ['', 'OWNED JAVA / DECOMPILER WORKERS       CPU% = one logical CPU per 100%', '    PID   CPU%    MiB  JOB']
        groups = []
        for service_file in [self.root / 'service.json', self.root / 'ahead-service.json', self.root / 'swarm.json', self.root / 'parallel-analysis.json', self.root / 'checkpoint-export.json', self.root / 'checkpoint-indirect.json', self.root / 'enrichment.json']:
            pointer = read_json(service_file)
            for unit in dict.fromkeys(u for u in (pointer.get('unit'), pointer.get('analysis_unit')) if u):
                result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ControlGroup', '--value'], capture_output=True, text=True)
                group = result.stdout.strip()
                if group:
                    groups.append(Path('/sys/fs/cgroup') / group.lstrip('/'))
        pids = set()
        for group in groups:
            for p in group.rglob('cgroup.procs'):
                try:
                    pids.update(p.read_text().split())
                except OSError:
                    pass
        workers = []
        for pid in pids:
            try:
                proc = Path('/proc') / pid
                comm = (proc / 'comm').read_text().strip()
                if comm not in ('java', 'decompile'):
                    continue
                stat = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
                used = int(stat[11]) + int(stat[12])
                prior = self.process_previous.get(pid)
                rate = 100 * (used - prior[1]) / self.ticks / (now - prior[0]) if prior else 0
                self.process_previous[pid] = now, used
                rss = int(stat[21]) * os.sysconf('SC_PAGE_SIZE') / 1024**2
                args = (proc / 'cmdline').read_text().split('\0')
                name = args[args.index('-process') + 1] if '-process' in args else comm
                workers.append((rate, f'{pid:>7} {rate:6.1f} {rss:6.0f}  {name}'))
            except (OSError, ValueError, IndexError):
                continue
        lines.append(f'Active processes: {len(workers)}  |  individual analysis passes may serialize internally')
        lines.extend(line for _, line in sorted(workers, reverse=True))
        return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--final-agent', action='store_true', help='Show final export, rate, capacity and audit status prominently')
    args = parser.parse_args()
    watcher = Watcher(args.directory.resolve())
    def sample():
        lines = watcher.sample()
        if not args.final_agent:
            return lines
        prefixes = ('FINAL ANALYSIS EXPORT:', 'FINAL EXPORT RATE:', 'FINAL SYMBOL EXPORT:', 'FINAL ANALYSIS GATE:',
                    'Phase:', 'Global queue:', 'Queue wait:', 'Capacity gates:', 'Host CPU busy',
                    'Working disk available', 'Disk guard:', 'Indexed programs:', 'Detailed gap audits:',
                    'Indirect data-flow shards:', 'lghub_agent_saved_wine.exe:')
        focused = ['DRAGONSHARD-2  |  FINAL AGENT EXPORT', '']
        for prefix in prefixes:
            focused.extend(line for line in lines if line.startswith(prefix))
        return focused + ['', 'Refreshes every 2 seconds. q / Ctrl-C closes ONLY this watcher.']
    if args.once:
        print('\n'.join(sample()))
        return
    def display(screen):
        curses.curs_set(0)
        screen.timeout(2000)
        while True:
            lines = sample()
            screen.erase()
            height, width = screen.getmaxyx()
            for row, line in enumerate(lines[:height - 1]):
                screen.addnstr(row, 0, line, max(1, width - 1))
            screen.refresh()
            if screen.getch() in (ord('q'), ord('Q')):
                break
    try:
        curses.wrapper(display)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
