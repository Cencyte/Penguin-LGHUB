#!/usr/bin/env python3
"""Analyze isolated copies of every campaign program, then export a new versioned corpus."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import sys
import threading
import time

from dragonshard import GIB, HERE, atomic_json, locks, preflight, source_state
from campaign import available, checked_batch, choose_workers, size, verify_and_clean_clones


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original_campaign', type=Path)
    parser.add_argument('--analysis-workers', type=int, default=4, choices=range(1, 9))
    parser.add_argument('--max-export-workers', type=int, default=32, choices=range(1, 49))
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    original = args.original_campaign.resolve()
    root = original / 'depth-v2'
    root.mkdir(exist_ok=True)
    if (root / 'owner.json').exists():
        owner = json.loads((root / 'owner.json').read_text())
        if not args.resume or owner.get('tool') != 'dragonshard-2-depth':
            raise RuntimeError('Existing depth campaign: inspected matching owner and --resume required')
        previous_pid = owner.get('pid')
        if isinstance(previous_pid, int) and previous_pid > 0 and Path(f'/proc/{previous_pid}').exists():
            raise RuntimeError('Previous coordinator PID still exists; verify it has exited before resuming')
    atomic_json(root / 'owner.json', {'tool': 'dragonshard-2-depth', 'pid': os.getpid()})
    campaign = json.loads((original / 'campaign.json').read_text())
    state = {'scope_programs': len(campaign['programs']), 'batches': [], 'state': 'starting'}
    catalog = {'schema': 1, 'complete': False, 'databases': []}
    stop = threading.Event()
    mutex = threading.Lock()
    active = {}

    def cancel(signum=None, frame=None):
        stop.set()
        with mutex:
            for p in active.values():
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, cancel)

    def execute(command, log_path, heap=None):
        if stop.is_set():
            raise RuntimeError('Depth campaign cancelled; evidence retained')
        env = dict(os.environ)
        if heap:
            env['GHIDRA_HEADLESS_MAXMEM'] = heap
        with log_path.open('w') as log:
            with mutex:
                if stop.is_set():
                    raise RuntimeError('Cancelled before launching child')
                p = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                     env=env, start_new_session=True)
                active[p.pid] = p
            try:
                while p.poll() is None:
                    if stop.is_set() or shutil.disk_usage(root).free < 8 * GIB:
                        cancel()
                        raise RuntimeError('Cancelled or disk reserve reached; partial evidence retained')
                    time.sleep(2)
                if p.returncode:
                    raise RuntimeError(f'Child exited {p.returncode}: {log_path}')
            finally:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
                    p.wait()
                with mutex:
                    active.pop(p.pid, None)

    def sage(label):
        result = subprocess.run([campaign['sage_python'], str(HERE / 'sage_probe.py'),
                                 '--command', campaign['sage_command']], capture_output=True,
                                text=True, timeout=45, check=True)
        atomic_json(root / f'sage-{label}.json', json.loads(result.stdout))

    def ahead_headroom():
        """Reserve the large ahead worker's remaining capacity, not only its current RSS."""
        service = root / 'ahead-service.json'
        if not service.exists():
            return 0
        unit = json.loads(service.read_text())['unit']
        result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '-p', 'MemoryCurrent'], capture_output=True, text=True)
        props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if props.get('ActiveState') != 'active':
            return 0
        current = props.get('MemoryCurrent', '0')
        return max(0, 18 - (int(current) / GIB if current.isdigit() else 0))

    def analyze(number, program, batch):
        # Reuse the completed, already verified real pilot instead of repeating it.
        pilot = root / 'coremessagingxp-pilot'
        ahead = root / 'ahead' / program['sha256'][:16]
        existing = batch / 'analysis' / program['sha256'][:16]
        if ahead.exists():
            directory = ahead
            report_path = directory / 'depth.json'
            while not (directory / 'complete.json').exists():
                if stop.is_set() or shutil.disk_usage(root).free < 8 * GIB:
                    raise RuntimeError('Stopped while waiting for ahead analysis')
                unit = json.loads((root / 'ahead-service.json').read_text())['unit']
                active_state = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--value'], capture_output=True, text=True).stdout.strip()
                if active_state not in ('active', 'activating'):
                    raise RuntimeError(f'Ahead analysis stopped without complete evidence: {directory}')
                time.sleep(2)
        elif args.resume and (existing / 'depth.json').exists():
            directory = existing
            report_path = directory / 'depth.json'
        elif number == 1 and program['name'] == 'CoreMessagingXP.dll' and (pilot / 'depth.json').is_file():
            directory = pilot
            report_path = directory / 'depth.json'
        else:
            directory = batch / 'analysis' / program['sha256'][:16]
            directory.mkdir(parents=True, exist_ok=False)
            source_manifest = json.loads((original / f'batch{number}/run/manifest.json').read_text())
            source = Path(source_manifest['project_dir'])
            before = source_state(source_manifest)
            if locks(source_manifest):
                raise RuntimeError('Saved baseline project unexpectedly open')
            for suffix in ('.gpr', '.rep'):
                subprocess.run(['cp', '--reflink=auto', '-r', '--',
                                str(source / (f'Batch{number}' + suffix)), str(directory)], check=True)
            if locks(source_manifest) or before != source_state(source_manifest):
                raise RuntimeError('Saved baseline changed during research clone')
            report_path = directory / 'depth.json'
            folder = str(PurePosixPath(program['project_path']).parent).strip('/')
            project = f'Batch{number}' + ('/' + folder if folder else '')
            command = [str(Path(campaign['ghidra']) / 'support/analyzeHeadless'), str(directory), project,
                       '-process', PurePosixPath(program['project_path']).name, '-noanalysis', '-max-cpu', '4',
                       '-scriptPath', str(HERE), '-postScript', 'DragonShardDeepen.java',
                       str(report_path), program['sha256'], 'analyze']
            execute(command, directory / 'analysis.log', '12G' if number == 4 else '4G')
        report = json.loads(report_path.read_text())
        if not report.get('complete') or report['sha256'] != program['sha256']:
            raise RuntimeError(f'Analysis report incomplete/wrong binary: {report_path}')
        if list(directory.glob(f'Batch{number}.lock*')):
            raise RuntimeError('Analysis project still locked; refuse staging')
        log = (directory / 'analysis.log').read_text()
        if f'Save succeeded for processed file: {program["project_path"]}' not in log:
            raise RuntimeError(f'No successful project save in {directory}')
        print(json.dumps({'analysis_finished': program['name'], 'before': report['before'], 'after': report['after']}), flush=True)
        return dict(program, source_project_dir=str(directory), source_project_name=f'Batch{number}',
                    analysis_audit=str(report_path))

    try:
        for number in range(1, 5):
            batch = root / f'batch{number}'
            batch.mkdir(exist_ok=args.resume)
            selected = json.loads((original / f'batch{number}/selection.json').read_text())
            if args.resume and (batch / 'run/coverage.json').exists():
                report = checked_batch(batch, selected['programs'])
                state['batches'].append({'batch': number, 'coverage': report})
                catalog['databases'].append(str(batch / 'run/corpus.sqlite'))
                atomic_json(root / 'catalog.json', catalog)
                atomic_json(root / 'status.json', dict(state, state=f'batch{number}_indexed'))
                verify_and_clean_clones(batch)
                continue
            sage(f'batch{number}-analysis')
            parallel = min(args.analysis_workers, len(selected['programs']), max(1, int((available() - 8 - ahead_headroom()) / 6)))
            already_ahead = all((root / 'ahead' / p['sha256'][:16]).exists() for p in selected['programs'])
            required = (0 if already_ahead else (16 if number == 4 else parallel * 6)) + 8 + ahead_headroom()
            if available() < required or shutil.disk_usage(root).free < 12 * GIB:
                raise RuntimeError(f'Insufficient resource reserve for batch {number} analysis')
            atomic_json(root / 'status.json', dict(state, state=f'batch{number}_analyzing'))
            analyzed = {}
            with ThreadPoolExecutor(max_workers=parallel) as pool:
                futures = {pool.submit(analyze, number, p, batch): p for p in selected['programs']}
                try:
                    for future in as_completed(futures):
                        program = future.result()
                        analyzed[program['sha256']] = program
                        atomic_json(batch / 'analyzed-programs.json', list(analyzed.values()))
                except BaseException:
                    cancel()
                    raise
            selected['programs'] = [analyzed[p['sha256']] for p in selected['programs']]
            # Compact only the newly analyzed programs into the next full-batch source.
            selected.update(run_dir=str(batch / 'run'), timeout_seconds=60)
            selection_path = batch / 'selection.json'
            atomic_json(selection_path, selected)
            (batch / 'source').mkdir(exist_ok=False)
            execute([str(Path(campaign['ghidra']) / 'support/analyzeHeadless'), str(batch / 'source'),
                     f'DeepBatch{number}', '-noanalysis', '-max-cpu', '1', '-scriptPath', str(HERE),
                     '-preScript', 'DragonShardStage.java', str(selection_path), str(batch / 'staging.json')],
                    batch / 'staging.log', '2G')
            staged = json.loads((batch / 'staging.json').read_text())
            if not staged.get('complete') or {p['sha256'] for p in staged['programs']} != set(analyzed):
                raise RuntimeError('Deep staging scope mismatch')
            counts = {p['sha256']: p['functions'] for p in staged['programs']}
            for program in selected['programs']:
                program['functions'] = counts[program['sha256']]
            # References and symbols make v2 larger than the initial C-only-like corpus.
            output_budget = 8 if number < 4 else 12
            # The completed 31-worker DLL campaign peaked at 15.5 GiB for the entire cgroup.
            # Keep >2x that per-worker average and retain 1.5 GiB for the much larger agent.
            worker_gib = 1.25 if number < 4 else 1.5
            workers = choose_workers(available() - ahead_headroom(), shutil.disk_usage(root).free / GIB,
                                     size(batch / 'source') / GIB, output_budget, args.max_export_workers, worker_gib)
            selected.update(project_dir=str(batch / 'source'), project_name=f'DeepBatch{number}',
                            workers=workers, worker_rss_gib=worker_gib, export_budget_gib=output_budget, reserve_disk_gib=8)
            atomic_json(batch / 'run-manifest.json', selected)
            report = preflight(selected)
            atomic_json(batch / 'preflight.json', report)
            if not report['ready']:
                raise RuntimeError(f'Deep export preflight blocked: {report["blockers"]}')
            atomic_json(root / 'status.json', dict(state, state=f'batch{number}_exporting', workers=workers))
            execute([sys.executable, str(HERE / 'dragonshard.py'), 'run', str(batch / 'run-manifest.json')],
                    batch / 'runner.log')
            report = checked_batch(batch, selected['programs'])
            for program in report:
                listing = program.get('listing_evidence', {})
                count = program['recognized_internal_functions']
                if listing.get('functions_with_instruction_metadata', 0) != count:
                    raise RuntimeError('Export lacks full listing-depth metadata; do not advertise a corrected corpus')
                if listing.get('zero_instruction_functions', count) > count * .01:
                    raise RuntimeError('Too many exported functions have no listing instructions')
            state['batches'].append({'batch': number, 'coverage': report})
            catalog['databases'].append(str(batch / 'run/corpus.sqlite'))
            atomic_json(root / 'catalog.json', catalog)
            atomic_json(root / 'status.json', dict(state, state=f'batch{number}_indexed'))
            verify_and_clean_clones(batch)
        catalog['complete'] = True
        atomic_json(root / 'catalog.json', catalog)
        atomic_json(root / 'status.json', dict(state, state='complete'))
    except BaseException as exc:
        cancel()
        atomic_json(root / 'status.json', dict(state, state='blocked_or_failed', reason=str(exc)))
        raise


if __name__ == '__main__':
    main()
