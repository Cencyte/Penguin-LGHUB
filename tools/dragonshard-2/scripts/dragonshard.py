#!/usr/bin/env python3
"""Plan/preflight/run a full DragonShard-2 corpus on independent reflink projects."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time

from corpus import build, inspect

GIB = 1024 ** 3
HERE = Path(__file__).resolve().parent


def atomic_json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.replace(path)


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def manifest(path):
    data = json.loads(Path(path).read_text())
    for key in ('project_dir', 'ghidra', 'run_dir', 'sage_python', 'sage_command'):
        data[key] = str(Path(data[key]).expanduser().absolute())
    if data.get('schema') != 2 or data['workers'] < 1 or data['timeout_seconds'] < 1:
        raise ValueError('Invalid schema, workers or timeout')
    if not data['programs'] or len({p['sha256'] for p in data['programs']}) != len(data['programs']):
        raise ValueError('Select a nonempty scope with distinct binary hashes')
    if Path(data['project_name']).name != data['project_name']:
        raise ValueError('project_name must be a name, not a path')
    project = Path(data['project_dir'])
    run = Path(data['run_dir'])
    if run.resolve().is_relative_to((project / (data['project_name'] + '.rep')).resolve()):
        raise ValueError('Run directory cannot be inside the source database')
    for p in data['programs']:
        pp = PurePosixPath(p['project_path'])
        if not pp.is_absolute() or '..' in pp.parts or any(c in pp.name for c in '*?'):
            raise ValueError(f'Unsafe project path: {pp}')
        if p['functions'] < 1 or len(p['sha256']) != 64:
            raise ValueError('Inventory needs a function estimate and full SHA256')
    return data


def jobs(data):
    # Roughly two jobs/worker; avoid starting 40 JVMs for every tiny DLL.
    chunk = max(1, math.ceil(sum(p['functions'] for p in data['programs']) / (data['workers'] * 2)))
    result = []
    for p in data['programs']:
        n = min(data['workers'], max(1, math.ceil(p['functions'] / chunk)))
        for i in range(n):
            result.append({'program': p, 'shard': i, 'shards': n,
                           'key': f'{p["sha256"][:16]}-{i:04d}-of-{n:04d}'})
    return result


def source_state(data):
    project = Path(data['project_dir'])
    rep = project / (data['project_name'] + '.rep')
    paths = [project / (data['project_name'] + '.gpr'), *sorted(rep.rglob('*'))]
    # A verified move to a lower-precision filesystem may round timestamps without
    # changing any byte. Preserve the established snapshot ID only for entries whose
    # size and copied timestamp still match the relocation record.
    marker = project / '.dragonshard-source-times.json'
    times = json.loads(marker.read_text()) if marker.exists() else {}
    result = []
    for p in paths:
        if not p.is_file():
            continue
        name, info = str(p.relative_to(project)), p.stat()
        copied = times.get(name, {})
        stamp = info.st_mtime_ns
        if copied.get('size') == info.st_size and copied.get('copied_mtime_ns') == stamp:
            stamp = copied['original_mtime_ns']
        result.append((name, info.st_size, stamp))
    return result


def locks(data):
    project = Path(data['project_dir'])
    return [str(p) for p in project.glob(data['project_name'] + '.lock*')]


def analysis_blockers(program):
    """Decompiler success alone cannot validate a program's listing/reference coverage."""
    path = program.get('analysis_audit')
    if not path:
        return ['Missing listing-depth audit; analyze/audit an isolated research copy before exporting']
    try:
        report = json.loads(Path(path).read_text())
        if report.get('sha256') != program['sha256'] or not report.get('complete'):
            return ['Incomplete or wrong-binary listing-depth audit']
        after = report['after']
        functions = after['functions']
        if functions < 1 or after['instruction_count'] < 1:
            return ['No analyzed functions/instructions in listing-depth audit']
        missing = after['functions_without_entry_instruction'] - after.get('synthetic_external_thunks_without_instructions', 0)
        if missing < 0:
            return ['Inconsistent synthetic thunk count in listing-depth audit']
        if missing > functions * .01:
            return [f'{missing}/{functions} functions have no entry instruction; research copy needs deeper analysis']
        return []
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f'Unreadable listing-depth audit: {exc}']


def preflight(data):
    report = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'workers': data['workers'], 'blockers': []}
    try:
        result = subprocess.run([data['sage_python'], str(HERE / 'sage_probe.py'), '--command', data['sage_command']],
                                capture_output=True, text=True, timeout=45, check=True)
        report['systemsage'] = json.loads(result.stdout)
        if not report['systemsage'].get('metrics'):
            raise ValueError('No SystemSage metrics')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report['blockers'].append(f'SystemSage MCP verification failed: {exc}')
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        mem[key] = int(value.split()[0]) * 1024
    report['available_gib'] = round(mem['MemAvailable'] / GIB, 2)
    report['swap_used_gib'] = round((mem['SwapTotal'] - mem['SwapFree']) / GIB, 2)
    report['memory_required_gib'] = data['workers'] * data.get('worker_rss_gib', 1.5) + data.get('reserve_memory_gib', 8)
    report['cpu_threads'] = os.cpu_count()
    report['pressure'] = Path('/proc/pressure/memory').read_text().strip()
    report['thermals_c'] = {}
    for sensor in Path('/sys/class/hwmon').glob('hwmon*/temp*_input'):
        try:
            name = (sensor.parent / 'name').read_text().strip()
            label_path = sensor.with_name(sensor.name.replace('_input', '_label'))
            label = label_path.read_text().strip() if label_path.exists() else sensor.stem
            report['thermals_c'][f'{sensor.parent.name}/{name}/{label}'] = int(sensor.read_text()) / 1000
        except (OSError, ValueError):
            pass
    arc_max = Path('/sys/module/zfs/parameters/zfs_arc_max')
    if arc_max.exists():
        report['arc_max_gib'] = round(int(arc_max.read_text()) / GIB, 2)
    if report['available_gib'] < report['memory_required_gib']:
        report['blockers'].append('Insufficient MemAvailable for configured workers plus reserve')
    run = Path(data['run_dir'])
    ancestor = run
    while not ancestor.exists():
        ancestor = ancestor.parent
    source = Path(data['project_dir']) / (data['project_name'] + '.rep')
    if not source.is_dir():
        report['blockers'].append(f'Missing source project: {source}')
        size = 0
    else:
        size = sum(p.stat().st_size for p in source.rglob('*') if p.is_file())
        report['copy_policy'] = 'reflink when available; independent full copy otherwise (full size budgeted)'
    report['source_logical_gib'] = round(size / GIB, 2)
    report['disk_free_gib'] = round(shutil.disk_usage(ancestor).free / GIB, 2)
    report['disk_required_gib'] = round(size * data['workers'] / GIB + data.get('export_budget_gib', 20) + data.get('reserve_disk_gib', 10), 2)
    if report['disk_free_gib'] < report['disk_required_gib']:
        report['blockers'].append('Insufficient disk for worst-case clone divergence, exports/index and reserve')
    report['source_locks'] = locks(data)
    if report['source_locks']:
        report['blockers'].append('Source project is open/locked; save and close it before cloning')
    if not (Path(data['ghidra']) / 'support/analyzeHeadless').is_file():
        report['blockers'].append('Missing analyzeHeadless')
    for p in data['programs']:
        for blocker in analysis_blockers(p):
            report['blockers'].append(f'{p["project_path"]}: {blocker}')
        binary = Path(p['binary_path'])
        if not binary.is_file() or digest(binary) != p['sha256']:
            report['blockers'].append(f'Binary missing/changed since inventory: {p["project_path"]}')
    report['ready'] = not report['blockers']
    return report


def run(data):
    report = preflight(data)
    print(json.dumps(report, indent=2), flush=True)
    if not report['ready']:
        raise ValueError('Full army not launched: fix the listed preflight blockers; scope/workers were not reduced')
    root = Path(data['run_dir'])
    # A run owns a new directory exclusively. Never clean/reuse an arbitrary existing tree.
    root.mkdir(parents=True, exist_ok=False)
    atomic_json(root / 'manifest.json', data)
    atomic_json(root / 'preflight.json', report)
    atomic_json(root / 'owner.json', {'tool': 'dragonshard-2', 'pid': os.getpid(), 'started': report['time']})
    for directory in ('clones', 'exports', 'logs'):
        (root / directory).mkdir()
    source = Path(data['project_dir'])
    state = source_state(data)
    for i in range(data['workers']):
        if locks(data) or source_state(data) != state:
            raise ValueError('Source project changed/reopened while cloning; run abandoned, original untouched')
        clone = root / 'clones' / f'{i:03d}'
        clone.mkdir()
        for suffix in ('.gpr', '.rep'):
            subprocess.run(['cp', '--reflink=auto', '-r', '--', str(source / (data['project_name'] + suffix)), str(clone)], check=True)
    if locks(data) or source_state(data) != state:
        raise ValueError('Source changed during clone creation; discard this run after inspection')
    atomic_json(root / 'source-state.json', state)
    snapshot_id = hashlib.sha256(json.dumps(state, separators=(',', ':')).encode()).hexdigest()
    planned = jobs(data)
    atomic_json(root / 'jobs.json', planned)
    pending = queue.Queue()
    for job in planned:
        pending.put(job)
    stop = threading.Event()
    active, mutex = {}, threading.Lock()
    def interrupt(signum, frame):
        stop.set()
        with mutex:
            for process in active.values():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
    previous = {s: signal.signal(s, interrupt) for s in (signal.SIGTERM, signal.SIGINT)}
    def worker(number):
        finished = 0
        while not stop.is_set():
            try:
                job = pending.get_nowait()
            except queue.Empty:
                return finished
            p = job['program']
            output = root / 'exports' / (job['key'] + '.jsonl')
            folder = str(PurePosixPath(p['project_path']).parent).strip('/')
            project_arg = data['project_name'] + ('/' + folder if folder else '')
            cmd = [str(Path(data['ghidra']) / 'support/analyzeHeadless'), str(root / 'clones' / f'{number:03d}'),
                   project_arg, '-process', PurePosixPath(p['project_path']).name, '-readOnly', '-noanalysis',
                   '-max-cpu', '1', '-scriptPath', str(HERE), '-postScript', 'DragonShardExport.java', str(output),
                   str(job['shard']), str(job['shards']), str(data['timeout_seconds']), p['sha256'], snapshot_id]
            env = dict(os.environ, GHIDRA_HEADLESS_MAXMEM=data.get('heap', '900m'))
            try:
                with (root / 'logs' / (job['key'] + '.log')).open('w') as log:
                    with mutex:
                        if stop.is_set():
                            return finished
                        process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
                        active[number] = process
                    rc = process.wait()
                    with mutex:
                        active.pop(number, None)
                if rc:
                    raise ValueError(f'{job["key"]}: headless exited {rc}; see log')
                header, end = inspect(output)
                if header['sha256'].lower() != p['sha256'].lower() or header['shard'] != job['shard'] or header['shards'] != job['shards']:
                    raise ValueError(f'Unexpected output identity: {output}')
                finished += 1
                print(json.dumps({'completed': job['key'], 'functions': end['attempted'], 'failures': end['failed'] + end['timeout']}), flush=True)
            except BaseException:
                stop.set()
                with mutex:
                    for other in active.values():
                        if other.poll() is None:
                            os.killpg(other.pid, signal.SIGTERM)
                raise
            finally:
                pending.task_done()
        return finished
    try:
        with ThreadPoolExecutor(max_workers=data['workers']) as pool:
            futures = [pool.submit(worker, i) for i in range(data['workers'])]
            for future in futures:
                future.result()
        if stop.is_set() or not pending.empty():
            raise ValueError('Run interrupted/incomplete; retain partial evidence and logs')
        coverage = build((root / 'exports').glob('*.jsonl'), root / 'corpus.sqlite', {p['sha256']: p for p in data['programs']})
        atomic_json(root / 'coverage.json', coverage)
        print(json.dumps({'corpus': str(root / 'corpus.sqlite'), 'coverage': coverage}), flush=True)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'preflight', 'run'))
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    data = manifest(args.manifest)
    if args.command == 'plan':
        planned = jobs(data)
        print(json.dumps({'workers': data['workers'], 'programs': len(data['programs']),
                          'recognized_functions': sum(p['functions'] for p in data['programs']),
                          'jobs': len(planned), 'scope': [p['project_path'] for p in data['programs']]}, indent=2))
    elif args.command == 'preflight':
        result = preflight(data)
        print(json.dumps(result, indent=2))
        return 0 if result['ready'] else 2
    else:
        run(data)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
