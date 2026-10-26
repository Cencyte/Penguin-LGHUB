#!/usr/bin/env python3
"""Trace computed-transfer data flow alongside an immutable checkpoint export."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading

from campaign import available
from dragonshard import HERE, GIB, atomic_json, digest
from swarm_tasks import evidence_summary


def run(args):
    source, root = args.export.resolve(), args.directory.resolve()
    manifest = json.loads((source / 'manifest.json').read_text())
    program = manifest['programs'][0]
    snapshot = source / 'checkpoint.gzf'
    expected = manifest['snapshot_id']
    if digest(snapshot) != expected:
        raise ValueError('Checkpoint identity changed')
    root.mkdir(parents=True, exist_ok=False)
    sage = subprocess.run([manifest['sage_python'], str(HERE / 'sage_probe.py'), '--command',
        manifest['sage_command']], check=True, capture_output=True, text=True, timeout=45)
    telemetry = json.loads(sage.stdout)
    # Native decompilers reached ~1.6 GiB each on the large agent, in addition
    # to the Java heap. DLL-worker RSS estimates under-budget this pass.
    if not telemetry.get('metrics') or available() < args.workers * 3 + 8:
        raise ValueError('SystemSage or RAM admission failed')
    if shutil.disk_usage(root).free < 24 * GIB:
        raise ValueError('Insufficient disk for 1 GiB evidence and the protected reserve')
    atomic_json(root / 'systemsage.json', telemetry)
    atomic_json(root / 'owner.json', dict(tool='dragonshard-2-packed-indirect', pid=os.getpid()))
    status = dict(state='running', complete=False, snapshot_id=expected, fixed_point_validated=False,
                  workers=args.workers, completed_shards=0, attempted_functions=0, scanned_functions=0)
    atomic_json(root / 'status.json', status)
    active, mutex, stopped = {}, threading.RLock(), threading.Event()

    def cancel(*_):
        stopped.set()
        with mutex:
            for process in active.values():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, cancel)

    def worker(shard):
        work = root / f'{shard:03d}'
        (work / 'driver').mkdir(parents=True)
        (work / 'temp').mkdir()
        request = work / 'request.json'
        output = work / 'evidence.jsonl'
        atomic_json(request, dict(sha256=program['sha256'], snapshot_id=expected,
                                  scan_shard=shard, scan_shards=args.workers))
        options = [f'-Djava.io.tmpdir={work / "temp"}', f'-Dpdb.cache.dir={source / "packed-cache"}',
                   f'-Ddragonshard.packed.path={snapshot}', '-Ddragonshard.analysis.stage=saved_checkpoint_before_fixed_point']
        command = [str(Path(manifest['ghidra']) / 'support/analyzeHeadless'), str(work / 'driver'), 'Driver',
                   '-noanalysis', '-max-cpu', '1', '-scriptPath', str(HERE), '-preScript',
                   'DragonShardIndirect.java', str(request), str(output), str(GIB // args.workers)]
        with (work / 'headless.log').open('w') as log:
            with mutex:
                if stopped.is_set():
                    raise RuntimeError('Cancelled')
                process = subprocess.Popen(command, env=dict(os.environ, GHIDRA_HEADLESS_MAXMEM='900m',
                    JAVA_TOOL_OPTIONS=' '.join(options)), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                active[shard] = process
            try:
                while process.poll() is None:
                    if stopped.wait(1) or shutil.disk_usage(root).free < 22 * GIB:
                        raise RuntimeError('Cancelled or disk reserve reached; partial evidence retained')
                if process.returncode:
                    raise RuntimeError(f'Shard {shard} exited {process.returncode}; see headless.log')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait()
                with mutex:
                    active.pop(shard, None)
        end = evidence_summary(output, program['sha256'], expected)
        with output.open() as stream:
            header = json.loads(next(stream))
            records = [json.loads(line) for line in stream]
        functions = [r for r in records if r['kind'] == 'function']
        if (header['scan_shard'] != shard or header['scan_shards'] != args.workers or
            header['total_enumerated'] != program['functions'] or
            header['scanned_functions'] != len(range(shard, program['functions'], args.workers)) or
            len(functions) != len({r['address'] for r in functions}) or
            len(functions) != header['selected_functions'] or len(functions) != end['attempted']):
            raise ValueError('Indirect scan coverage/identity mismatch')
        return header, end

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(worker, i) for i in range(args.workers)]
            try:
                for future in as_completed(futures):
                    header, end = future.result()
                    status['completed_shards'] += 1
                    status['attempted_functions'] += end['attempted']
                    status['scanned_functions'] += header['scanned_functions']
                    atomic_json(root / 'status.json', status)
            except BaseException:
                cancel()
                raise
        if digest(snapshot) != expected:
            raise ValueError('Checkpoint changed')
        status.update(state='complete', complete=True)
        atomic_json(root / 'status.json', status)
    except BaseException as error:
        cancel()
        status.update(state='blocked_or_failed', error=str(error))
        atomic_json(root / 'status.json', status)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.workers <= (os.cpu_count() or 1):
        parser.error('Workers must fit the host CPU count')
    run(args)
