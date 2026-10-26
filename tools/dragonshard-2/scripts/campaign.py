#!/usr/bin/env python3
"""Continue the full corpus in priority batches, preserving completed evidence between runs."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from dragonshard import GIB, HERE, atomic_json, preflight
from corpus import connect


def available():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024 / GIB
    raise RuntimeError('MemAvailable missing')


def size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def choose_workers(memory_gib, disk_gib, project_gib, output_gib, max_workers=32, worker_gib=1.5):
    # Full corpus is unchanged. Resource checks govern only concurrent workers.
    if worker_gib <= 0:
        raise ValueError('Worker memory budget must be positive')
    ram = math.floor((memory_gib - 10) / worker_gib)
    disk = math.floor((disk_gib - output_gib - 8) / max(project_gib, 0.001))
    n = min(max_workers, os.cpu_count() or 1, ram, disk)
    if n < 1:
        raise RuntimeError(f'Insufficient resources even for one worker: available={memory_gib:.2f} GiB, disk={disk_gib:.2f} GiB, output budget={output_gib:.2f} GiB')
    return n


def guarded_run(command, log, root):
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    try:
        while process.poll() is None:
            if shutil.disk_usage(root).free < 8 * GIB:
                raise RuntimeError('Disk reserve reached; stopping owned workers and retaining partial evidence')
            time.sleep(5)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
    except BaseException:
        if process.poll() is None:
            # The runner catches SIGTERM and terminates its tracked worker groups.
            process.send_signal(signal.SIGTERM)
            process.wait()
        raise


def checked_batch(batch, expected):
    report = json.loads((batch / 'run/coverage.json').read_text())
    if {r['sha256'] for r in report} != {p['sha256'] for p in expected}:
        raise RuntimeError('Coverage program scope mismatch')
    if not all(r['export_complete'] for r in report):
        raise RuntimeError('Incomplete batch')
    db = connect(batch / 'run/corpus.sqlite')
    try:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Batch index integrity failure')
        if db.execute('SELECT count(*) FROM functions').fetchone()[0] != sum(r['recognized_internal_functions'] for r in report):
            raise RuntimeError('Coverage disagrees with indexed functions')
    finally:
        db.close()
    return report


def verify_and_clean_clones(batch):
    """Delete only this finished run's disposable replicas, retaining all research evidence."""
    run = batch / 'run'
    owner = json.loads((run / 'owner.json').read_text())
    manifest = json.loads((run / 'manifest.json').read_text())
    if owner.get('tool') != 'dragonshard-2' or Path(manifest['run_dir']).resolve() != run.resolve():
        raise RuntimeError('Cannot prove clone ownership')
    checked_batch(batch, manifest['programs'])
    clones = run / 'clones'
    if not clones.exists():
        return
    if clones.is_symlink() or clones.resolve().parent != run.resolve():
        raise RuntimeError('Unsafe clone path')
    needle = str(clones.resolve()).encode()
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if needle in (proc / 'cmdline').read_bytes():
                raise RuntimeError(f'Worker still references clone directory: {proc.name}')
        except (FileNotFoundError, ProcessLookupError):
            pass
        except PermissionError:
            # Workers are launched as this user; unrelated processes may hide their command line.
            continue
    shutil.rmtree(clones)
    atomic_json(run / 'clones-reclaimed.json', {'verified_complete': True, 'removed': str(clones)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--first-unit', required=True)
    parser.add_argument('--max-workers', type=int, default=32)
    args = parser.parse_args()
    root = args.directory.resolve()
    campaign = json.loads((root / 'campaign.json').read_text())
    status = {'scope_programs': len(campaign['programs']), 'recognized_functions_planned': sum(p['functions'] for p in campaign['programs']), 'batches': []}
    state_path = root / 'status.json'
    catalog = {'schema': 1, 'complete': False, 'databases': []}
    # Supervise the first, already launched service. Fail closed if it fails.
    while True:
        # Successful transient units may already have been garbage-collected by systemd.
        # Durable, validated output is authoritative even when the unit no longer exists.
        first = root / 'batch1'
        if (first / 'run/coverage.json').exists() and (first / 'run/corpus.sqlite').exists():
            selected = json.loads((first / 'selection.json').read_text())
            checked_batch(first, selected['programs'])
            break
        props = subprocess.check_output(['systemctl', '--user', 'show', args.first_unit, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'Result', '-p', 'ExecMainStatus'], text=True)
        state = dict(line.split('=', 1) for line in props.splitlines() if '=' in line)
        if state.get('LoadState') == 'not-found':
            atomic_json(state_path, dict(status, state='blocked_or_failed', reason='First batch service missing'))
            raise RuntimeError('First batch service missing')
        if state.get('ActiveState') in ('inactive', 'failed'):
            if state.get('Result') != 'success' or state.get('ExecMainStatus') != '0':
                atomic_json(state_path, dict(status, state='blocked_or_failed', reason=f'First batch service failed: {state}'))
                raise RuntimeError(f'First batch service failed: {state}')
            break
        if shutil.disk_usage(root).free < 8 * GIB:
            subprocess.run(['systemctl', '--user', 'stop', args.first_unit], check=True)
            atomic_json(state_path, dict(status, state='blocked_or_failed', reason='Disk reserve reached during batch1'))
            raise RuntimeError('Disk reserve reached; first batch stopped, evidence retained')
        atomic_json(state_path, dict(status, state='batch1_running'))
        time.sleep(5)
    observed_bytes_per_function = 0
    try:
        for number in range(1, 5):
            batch = root / f'batch{number}'
            selection = json.loads((batch / 'selection.json').read_text())
            if number > 1:
                atomic_json(state_path, dict(status, state=f'batch{number}_staging'))
                # Real SystemSage MCP call immediately before each staging operation.
                sage = subprocess.run([selection['sage_python'], str(HERE / 'sage_probe.py'), '--command', selection['sage_command']], capture_output=True, text=True, timeout=45, check=True)
                atomic_json(batch / 'sage-before-staging.json', json.loads(sage.stdout))
                if available() < 4 or shutil.disk_usage(root).free < 10 * GIB:
                    raise RuntimeError('Not enough staging reserve; completed batches retained')
                report_path = batch / 'staging.json'
                if not report_path.exists():
                    cmd = [str(Path(selection['ghidra']) / 'support/analyzeHeadless'), str(batch / 'source'), f'Batch{number}',
                           '-noanalysis', '-max-cpu', '1', '-scriptPath', str(HERE), '-preScript', 'DragonShardStage.java',
                           str(batch / 'selection.json'), str(report_path)]
                    with (batch / 'staging.log').open('w') as log:
                        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, GHIDRA_HEADLESS_MAXMEM='1600m'), check=True)
                staged = json.loads(report_path.read_text())
                if not staged.get('complete') or {p['sha256'] for p in staged['programs']} != {p['sha256'] for p in selection['programs']}:
                    raise RuntimeError(f'Invalid staging report: batch {number}')
                counts = {p['sha256']: p['functions'] for p in staged['programs']}
                for program in selection['programs']:
                    program['functions'] = counts[program['sha256']]
                count = sum(p['functions'] for p in selection['programs'])
                output_budget = max(6, math.ceil(observed_bytes_per_function * count * 1.5 / GIB))
                project_size = size(batch / 'source') / GIB
                workers = choose_workers(available(), shutil.disk_usage(root).free / GIB, project_size, output_budget, args.max_workers)
                selection.update(project_dir=str(batch / 'source'), project_name=f'Batch{number}', run_dir=str(batch / 'run'),
                                 workers=workers, export_budget_gib=output_budget, reserve_disk_gib=8)
                atomic_json(batch / 'run-manifest.json', selection)
                report = preflight(selection)
                atomic_json(batch / 'preflight.json', report)
                if not report['ready']:
                    raise RuntimeError(f'Batch {number} preflight: {report["blockers"]}')
                atomic_json(state_path, dict(status, state=f'batch{number}_running', workers=workers))
                print(json.dumps({'start_batch': number, 'workers': workers, 'programs': len(selection['programs']), 'functions': count}), flush=True)
                with (batch / 'runner.log').open('w') as log:
                    guarded_run([sys.executable, str(HERE / 'dragonshard.py'), 'run', str(batch / 'run-manifest.json')], log, root)
            report = checked_batch(batch, selection['programs'])
            n = sum(r['recognized_internal_functions'] for r in report)
            evidence_bytes = size(batch / 'run/exports') + (batch / 'run/corpus.sqlite').stat().st_size
            observed_bytes_per_function = max(observed_bytes_per_function, evidence_bytes / max(n, 1))
            status['batches'].append({'batch': number, 'coverage': report})
            catalog['databases'].append(str(batch / 'run/corpus.sqlite'))
            atomic_json(root / 'catalog.json', catalog)
            atomic_json(state_path, dict(status, state=f'batch{number}_indexed'))
            verify_and_clean_clones(batch)
            print(json.dumps({'completed_batch': number, 'functions': n, 'evidence_gib': round(evidence_bytes/GIB, 3)}), flush=True)
        catalog['complete'] = True
        atomic_json(root / 'catalog.json', catalog)
        atomic_json(state_path, dict(status, state='complete'))
    except BaseException as exc:
        atomic_json(state_path, dict(status, state='blocked_or_failed', reason=str(exc)))
        raise


if __name__ == '__main__':
    main()
