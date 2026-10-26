#!/usr/bin/env python3
"""Export an audited saved checkpoint in independent JVMs while later analysis continues.

The immutable packed backing is shared; ProgramDB handles, caches and decompilers
are private. This corpus never claims that the producer's fixed point is complete.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time

from campaign import available
from corpus import build, inspect
from dragonshard import HERE, GIB, analysis_blockers, atomic_json, digest


def shard_budget(total, workers, shard):
    symbols = total // 8
    return (total - symbols) // workers + (symbols if shard == 0 else 0)


def validate_checkpoint(checkpoint, program):
    report = json.loads((checkpoint / 'report.json').read_text())
    if not report.get('complete') or report.get('mode') != 'finish' or report.get('sha256') != program['sha256']:
        raise ValueError('Only a complete same-binary remaining-analyzer checkpoint may be exported')
    audit = checkpoint / 'depth.json'
    blockers = analysis_blockers(dict(program, analysis_audit=str(audit)))
    if blockers:
        raise ValueError('; '.join(blockers))
    if not (checkpoint / 'program.gzf').is_file():
        raise ValueError('Saved packed checkpoint is missing')
    return json.loads(audit.read_text())['after']['functions']


def run(args):
    manifest = json.loads(args.manifest.read_text())
    if len(manifest['programs']) != 1:
        raise ValueError('Select exactly one audited checkpoint binary')
    program = manifest['programs'][0]
    count = validate_checkpoint(args.checkpoint, program)
    root = args.directory.resolve()
    if ' ' in str(root):
        raise ValueError('Work directory cannot contain spaces in JVM option transport')
    root.mkdir(parents=True, exist_ok=False)
    snapshot = root / 'checkpoint.gzf'
    source = args.checkpoint / 'program.gzf'
    before = digest(source)
    subprocess.run(['cp', '--reflink=auto', '--', str(source), str(snapshot)], check=True)
    if digest(snapshot) != before or digest(source) != before:
        raise ValueError('Checkpoint changed during staging')
    shutil.copy2(args.checkpoint / 'depth.json', root / 'depth.json')
    sage = subprocess.run([manifest['sage_python'], str(HERE / 'sage_probe.py'), '--command',
                           manifest['sage_command']], check=True, capture_output=True, text=True, timeout=45)
    telemetry = json.loads(sage.stdout)
    if not telemetry.get('metrics'):
        raise ValueError('SystemSage returned no metrics')
    atomic_json(root / 'systemsage.json', telemetry)
    if available() < args.workers * args.worker_gib + 8:
        raise ValueError('Insufficient RAM for the requested army and host reserve')
    commitment = (args.raw_gib + args.index_gib + args.reserve_gib + args.other_work_gib + 3) * GIB
    if shutil.disk_usage(root).free < commitment:
        raise ValueError('Disk cannot cover raw evidence, index, cache, other work, and reserve')
    if digest(program['binary_path']) != program['sha256']:
        raise ValueError('Executable identity changed')
    atomic_json(root / 'owner.json', dict(tool='dragonshard-2-packed-export', pid=os.getpid()))
    manifest.update(run_dir=str(root), workers=args.workers, programs=[dict(program, functions=count)],
                    snapshot_id=before, analysis_stage='saved_checkpoint_before_fixed_point')
    atomic_json(root / 'manifest.json', manifest)
    state = dict(state='warming_cache', complete=False, fixed_point_validated=False,
                 functions=count, snapshot_id=before, requested_workers=args.workers,
                 completed_shards=0, exported_functions=0, active_workers=0)
    atomic_json(root / 'status.json', state)
    for name in ('exports', 'workers', 'packed-cache'):
        (root / name).mkdir()
    stop = threading.Event()
    active, mutex = {}, threading.RLock()

    def cancel(*_):
        stop.set()
        with mutex:
            for process in active.values():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, cancel)

    def persist(**updates):
        with mutex:
            state.update(updates)
            atomic_json(root / 'status.json', state)

    def launch(label, script, arguments, extra=()):
        folder = root / 'workers' / label
        folder.mkdir()
        for name in ('driver', 'temp'):
            (folder / name).mkdir()
        options = [f'-Djava.io.tmpdir={folder / "temp"}', f'-Dpdb.cache.dir={root / "packed-cache"}', *extra]
        env = dict(os.environ, GHIDRA_HEADLESS_MAXMEM='900m', JAVA_TOOL_OPTIONS=' '.join(options))
        command = [str(Path(manifest['ghidra']) / 'support/analyzeHeadless'), str(folder / 'driver'),
                   'Driver', '-noanalysis', '-max-cpu', '1', '-scriptPath', str(HERE), '-preScript', script, *arguments]
        with (folder / 'headless.log').open('w') as log:
            with mutex:
                if stop.is_set():
                    raise RuntimeError('Export cancelled')
                process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                active[label] = process
                persist(active_workers=len(active))
            try:
                while process.poll() is None:
                    if stop.wait(1):
                        raise RuntimeError('Export cancelled')
                    if shutil.disk_usage(root).free < args.reserve_gib * GIB:
                        raise RuntimeError('Disk reserve reached; partial evidence retained')
                if process.returncode:
                    raise RuntimeError(f'{label}: headless exit {process.returncode}; see {folder / "headless.log"}')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait()
                with mutex:
                    active.pop(label, None)
                    persist(active_workers=len(active))

    try:
        warm = root / 'warm-request.json'
        atomic_json(warm, dict(mode='warm_cache', sha256=program['sha256'], output=str(root),
                              cache_paths=[str(snapshot)]))
        launch('warm', 'DragonShardAnalyze.java', [str(warm)])
        if not json.loads((root / 'report.json').read_text()).get('complete'):
            raise RuntimeError('Immutable backing did not warm successfully')
        persist(state='exporting_checkpoint')
        def worker(shard):
            output = root / 'exports' / f'{shard:04d}.jsonl'
            launch(f'{shard:04d}', 'DragonShardExport.java', [str(output), str(shard), str(args.workers),
                   '60', program['sha256'], before, str(shard_budget(int(args.raw_gib * GIB), args.workers, shard))],
                   [f'-Ddragonshard.packed.path={snapshot}', f'-Ddragonshard.project.path={program["project_path"]}',
                    '-Ddragonshard.analysis.stage=saved_checkpoint_before_fixed_point'])
            if not output.exists():
                log = root / 'workers' / f'{shard:04d}' / 'headless.log'
                errors = [line for line in log.read_text(errors='replace').splitlines() if 'ERROR' in line]
                raise RuntimeError(f'Shard {shard} produced no completed export: {errors[-1:]}; see {log}')
            header, end = inspect(output)
            if (header['sha256'] != program['sha256'] or header['snapshot_id'] != before or
                header['shard'] != shard or header['shards'] != args.workers or end['total_enumerated'] != count):
                raise ValueError('Checkpoint shard identity or coverage differs')
            return end
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pilot = min(1, args.workers - 1)
            futures = [pool.submit(worker, pilot)]
            try:
                # Compile/open/decompile real evidence once before launching the army.
                # This shard remains part of the final coverage; no throwaway production work.
                pilot_output = root / 'exports' / f'{pilot:04d}.jsonl.partial'
                while not futures[0].done():
                    if pilot_output.exists() and pilot_output.stat().st_size > 8192:
                        break
                    if stop.wait(1):
                        raise RuntimeError('Cancelled during first-shard validation')
                if futures[0].done():
                    futures[0].result()
                futures.extend(pool.submit(worker, i) for i in range(args.workers) if i != pilot)
                failures = []
                for future in as_completed(futures):
                    try:
                        end = future.result()
                    except Exception as error:
                        failures.append(str(error))
                        persist(shard_failures=failures)
                        continue
                    persist(completed_shards=state['completed_shards'] + 1,
                            exported_functions=state['exported_functions'] + end['attempted'])
                if failures:
                    raise RuntimeError('; '.join(failures))
            except BaseException:
                cancel()
                raise
        if digest(snapshot) != before:
            raise ValueError('Immutable packed checkpoint changed during export')
        persist(state='waiting_for_index_space')
        while shutil.disk_usage(root).free < (args.index_gib + args.reserve_gib) * GIB:
            if stop.wait(5):
                raise RuntimeError('Cancelled before indexing; raw evidence retained')
        persist(state='indexing_checkpoint')
        coverage = build((root / 'exports').glob('*.jsonl'), root / 'corpus.sqlite',
                         {program['sha256']: program}, max_bytes=int(args.index_gib * GIB))
        atomic_json(root / 'coverage.json', coverage)
        previous = json.loads(args.catalog.read_text())['databases'] if args.catalog else []
        atomic_json(root / 'catalog.json', dict(complete=False, checkpoint_export_complete=True,
                    fixed_point_validated=False, databases=[*previous, str(root / 'corpus.sqlite')],
                    snapshot_id=before, scope='Earlier deep corpus plus saved-checkpoint evidence; final reconciliation pending'))
        persist(state='complete', complete=True, fixed_point_validated=False, database=str(root / 'corpus.sqlite'))
    except BaseException as error:
        cancel()
        persist(state='blocked_or_failed', error=str(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--workers', type=int, default=42)
    parser.add_argument('--worker-gib', type=float, default=1.25)
    parser.add_argument('--raw-gib', type=float, default=8)
    parser.add_argument('--index-gib', type=float, default=12)
    parser.add_argument('--other-work-gib', type=float, default=24)
    parser.add_argument('--reserve-gib', type=float, default=22)
    args = parser.parse_args()
    if not 1 <= args.workers <= (os.cpu_count() or 1):
        parser.error('Worker count must fit the host CPU count')
    if any(not math.isfinite(n) or n <= 0 for n in
           (args.worker_gib, args.raw_gib, args.index_gib, args.other_work_gib, args.reserve_gib)):
        parser.error('Resource budgets must be finite and positive')
    run(args)
