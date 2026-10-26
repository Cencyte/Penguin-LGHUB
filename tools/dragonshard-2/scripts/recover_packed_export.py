#!/usr/bin/env python3
"""Preserve valid export prefixes and distribute only missing ordinals across JVMs."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading

from campaign import available
from corpus import build, inspect
from dragonshard import HERE, GIB, atomic_json, digest


def prefix(path, expected, output=None, segment=None, total=None):
    """Accept complete JSON lines only, with exact contiguous ordinal provenance."""
    counts = Counter()
    addresses = set()
    accepted = 0
    end = None
    with Path(path).open('rb') as stream:
        raw = stream.readline()
        header = json.loads(raw)
        for key, value in expected.items():
            if header.get(key) != value:
                raise ValueError(f'{path}: wrong {key}')
        if header.get('kind') != 'program' or header.get('schema') != 2:
            raise ValueError('Unsupported export header')
        n, shard = header['shards'], header['shard']
        if not 0 <= shard < n:
            raise ValueError('Invalid partition')
        first = shard
        # Ghidra's reported count includes external functions; the export iterator does not.
        total = header['reported_function_count'] if total is None else total
        limit = total
        if segment:
            if not header.get('segment') or any(header.get(k) != v for k, v in segment.items()):
                raise ValueError('Segment bounds differ')
            first = shard + max(0, math.ceil((segment['ordinal_start'] - shard) / n)) * n
            limit = min(limit, segment['ordinal_end'])
        elif header.get('segment'):
            raise ValueError('Segment supplied as a whole-shard prefix')
        next_ordinal = first
        if output:
            output.write(raw)
        accepted += len(raw)
        for raw in stream:
            if end is not None:
                raise ValueError('Records after completion')
            if not raw.endswith(b'\n'):
                # A SIGTERM may interrupt the writer in the middle of its final record.
                break
            row = json.loads(raw)
            kind = row.get('kind')
            if kind == 'symbol':
                if shard != 0 or segment or next_ordinal != first:
                    raise ValueError('Symbol outside the initial symbol section')
            elif kind == 'function':
                status = row.get('status')
                if (row.get('ordinal') != next_ordinal or next_ordinal >= limit or
                    row.get('address') in addresses or status not in ('ok', 'failed', 'timeout') or
                    (status == 'ok' and not row.get('c'))):
                    raise ValueError('Invalid, duplicated, or noncontiguous function evidence')
                addresses.add(row['address'])
                counts[status] += 1
                next_ordinal += n
            elif kind == 'complete':
                if (row.get('attempted') != sum(counts.values()) or
                    any(row.get(k) != counts[k] for k in ('ok', 'failed', 'timeout')) or
                    row.get('total_enumerated') != total or next_ordinal < limit):
                    raise ValueError('Invalid completion coverage')
                end = row
                continue
            else:
                raise ValueError('Unexpected evidence row')
            if output:
                output.write(raw)
            accepted += len(raw)
    if segment and end is None:
        raise ValueError('Segment did not complete')
    return dict(header=header, counts=dict(counts), functions=sum(counts.values()),
                next_ordinal=next_ordinal, accepted_bytes=accepted, complete=end is not None)


def split_tails(prefixes, total, workers):
    missing = sum(len(range(p['next_ordinal'], total, p['header']['shards'])) for p in prefixes.values())
    chunk = max(1, math.ceil(missing / max(1, workers * 2)))
    jobs = []
    for shard, p in sorted(prefixes.items()):
        step = p['header']['shards']
        for first in range(p['next_ordinal'], total, step * chunk):
            jobs.append(dict(shard=shard, ordinal_start=first, ordinal_end=min(total, first + step * chunk)))
    return jobs


def check_symbols(path, expected, total):
    with path.open() as stream:
        header = json.loads(next(stream))
        if not header.get('symbols_only') or any(header.get(k) != v for k, v in expected.items()):
            raise ValueError('Wrong symbol evidence identity')
        ended = False
        symbols = 0
        for line in stream:
            row = json.loads(line)
            if ended:
                raise ValueError('Trailing symbol evidence')
            if row['kind'] == 'symbol':
                symbols += 1
            elif row['kind'] == 'complete' and row['attempted'] == 0 and row['total_enumerated'] == total:
                ended = True
            else:
                raise ValueError('Invalid symbol export row')
        if not ended:
            raise ValueError('Incomplete symbols')
    return dict(functions=0, symbols=symbols)


def run(args):
    source, root = args.source.resolve(), args.directory.resolve()
    prior = json.loads((source / 'status.json').read_text())
    if prior.get('state') != 'blocked_or_failed':
        raise ValueError('Recovery requires a stopped failed export')
    unit_state = subprocess.check_output(['systemctl', '--user', 'show', args.source_unit,
                                        '-p', 'ActiveState', '--value'], text=True).strip()
    if unit_state not in ('inactive', 'failed'):
        raise ValueError('Source export service is still active')
    owner = json.loads((source / 'owner.json').read_text())
    if owner.get('tool') != 'dragonshard-2-packed-export':
        raise ValueError('Source is not an owned checkpoint export')
    manifest = json.loads((source / 'manifest.json').read_text())
    program = manifest['programs'][0]
    total, shards, snapshot = program['functions'], manifest['workers'], source / 'checkpoint.gzf'
    with next((source / 'exports').glob('*.jsonl')).open() as stream:
        source_header = json.loads(next(stream))
    expected = dict(sha256=program['sha256'], snapshot_id=manifest['snapshot_id'], shards=shards,
                    reported_function_count=source_header['reported_function_count'], project_path=program['project_path'])
    if digest(snapshot) != expected['snapshot_id']:
        raise ValueError('Packed source changed')
    root.mkdir(parents=True, exist_ok=False)
    for folder in ('exports', 'prefixes', 'segments', 'workers'):
        (root / folder).mkdir()
    atomic_json(root / 'owner.json', dict(tool='dragonshard-2-export-recovery', pid=os.getpid(), source=str(source)))
    atomic_json(root / 'manifest.json', dict(manifest, run_dir=str(root)))
    state = dict(state='validating_saved_evidence', complete=False, fixed_point_validated=False,
                 functions=total, snapshot_id=expected['snapshot_id'], requested_workers=shards,
                 recovery_worker_limit=args.workers, active_workers=0, completed_shards=0,
                 exported_functions=0, source=str(source))
    atomic_json(root / 'status.json', state)
    sage = subprocess.run([manifest['sage_python'], str(HERE / 'sage_probe.py'), '--command',
                           manifest['sage_command']], capture_output=True, text=True, check=True, timeout=45)
    telemetry = json.loads(sage.stdout)
    if not telemetry.get('metrics'):
        raise ValueError('SystemSage has no metrics')
    atomic_json(root / 'systemsage.json', telemetry)
    stop, mutex, active = threading.Event(), threading.RLock(), {}

    def persist(**updates):
        with mutex:
            state.update(updates)
            atomic_json(root / 'status.json', state)

    def cancel(*_):
        stop.set()
        with mutex:
            for process in active.values():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, cancel)
    try:
        prefixes = {}
        for shard in range(shards):
            completed = source / 'exports' / f'{shard:04d}.jsonl'
            if completed.exists():
                header, end = inspect(completed)
                if end['total_enumerated'] != total or any(header.get(k) != v for k, v in dict(expected, shard=shard).items()):
                    raise ValueError('Completed export identity differs')
                (root / 'exports' / completed.name).symlink_to(completed)
                state['completed_shards'] += 1
                state['exported_functions'] += end['attempted']
            else:
                partial = source / 'exports' / f'{shard:04d}.jsonl.partial'
                saved = root / 'prefixes' / f'{shard:04d}.jsonl.partial'
                with saved.open('wb') as out:
                    p = prefix(partial, dict(expected, shard=shard), out, total=total)
                # A first function proves shard zero finished the preceding symbol table.
                if shard == 0 and not p['functions']:
                    # The symbol table itself exceeded the old cap. Regenerate it with
                    # a dedicated budget; retain the original partial as failure evidence.
                    p['symbols_incomplete'] = True
                    saved.write_text(json.dumps(p['header'], separators=(',', ':'))+'\n')
                prefixes[shard] = p
                state['exported_functions'] += p['functions']
                (root / 'exports' / saved.name).symlink_to(saved)
        jobs = split_tails(prefixes, total, args.workers)
        if prefixes.get(0, {}).get('symbols_incomplete'):
            jobs.insert(0, dict(shard=0, ordinal_start=0, ordinal_end=total, symbols_only=True))
        atomic_json(root / 'prefixes.json', {str(k): v for k, v in prefixes.items()})
        atomic_json(root / 'jobs.json', jobs)
        # This replaces the uneven whole-shard limit with small function-only segments.
        # Each segment gets its own bounded allocation; symbols are reused once.
        raw_cap = 4 * GIB
        if shutil.disk_usage(root).free < (22 + 12 + 24) * GIB + raw_cap:
            raise ValueError('Cannot cover recovery output, index, other work, and disk reserve')
        concurrent = min(args.workers, len(jobs), max(0, int((available() - 8) / 1.05)))
        if jobs and concurrent < 1:
            raise ValueError('No RAM admission capacity')
        persist(state='recovering_missing_functions', missing_functions=total-state['exported_functions'],
                recovered_prefix_functions=sum(p['functions'] for p in prefixes.values()),
                recovery_segments=len(jobs), completed_segments=0, admitted_workers=concurrent)

        def worker(number, job):
            work = root / 'workers' / f'{number:04d}'
            (work / 'driver').mkdir(parents=True)
            (work / 'temp').mkdir()
            output = root / 'segments' / f'{number:04d}.jsonl'
            opts = [f'-Djava.io.tmpdir={work / "temp"}', f'-Dpdb.cache.dir={source / "packed-cache"}',
                    f'-Ddragonshard.packed.path={snapshot}', f'-Ddragonshard.project.path={program["project_path"]}',
                    '-Ddragonshard.analysis.stage=saved_checkpoint_before_fixed_point', '-Ddragonshard.export.segment=true',
                    f'-Ddragonshard.ordinal.start={job["ordinal_start"]}', f'-Ddragonshard.ordinal.end={job["ordinal_end"]}']
            if job.get('symbols_only'):
                opts.append('-Ddragonshard.export.symbolsOnly=true')
            limit = GIB if job.get('symbols_only') else (raw_cap-GIB) // max(1,len(jobs))
            command = [str(Path(manifest['ghidra']) / 'support/analyzeHeadless'), str(work / 'driver'), 'Driver',
                       '-noanalysis', '-max-cpu', '1', '-scriptPath', str(HERE), '-preScript', 'DragonShardExport.java',
                       str(output), str(job['shard']), str(shards), '60', program['sha256'], expected['snapshot_id'],
                       str(limit)]
            with (work / 'headless.log').open('w') as log:
                with mutex:
                    if stop.is_set():
                        raise RuntimeError('Cancelled')
                    process = subprocess.Popen(command, env=dict(os.environ, GHIDRA_HEADLESS_MAXMEM='640m',
                        JAVA_TOOL_OPTIONS=' '.join(opts)), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    active[number] = process
                    persist(active_workers=len(active))
                try:
                    while process.poll() is None:
                        if stop.wait(1) or shutil.disk_usage(root).free < 22 * GIB:
                            raise RuntimeError('Cancelled or disk reserve reached')
                    if process.returncode or not output.exists():
                        raise RuntimeError(f'Segment {number} failed; inspect {work / "headless.log"}')
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                    process.wait()
                    with mutex:
                        active.pop(number, None)
                        persist(active_workers=len(active))
            if job.get('symbols_only'):
                return check_symbols(output, dict(expected, shard=0), total)
            return prefix(output, dict(expected, shard=job['shard']),
                          segment={k: job[k] for k in ('ordinal_start', 'ordinal_end')}, total=total)

        failures = []
        if jobs:
            with ThreadPoolExecutor(max_workers=concurrent) as pool:
                futures = {pool.submit(worker, i, job): i for i, job in enumerate(jobs)}
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        persist(completed_segments=state['completed_segments']+1,
                                exported_functions=state['exported_functions']+result['functions'])
                        if state['exported_functions'] == total and state['completed_segments'] < len(jobs):
                            persist(state='exporting_symbol_table')
                    except Exception as error:
                        failures.append(str(error))
                        persist(segment_failures=failures)
        if failures:
            raise RuntimeError('; '.join(failures))
        persist(state='assembling_verified_shards')
        for shard, p in prefixes.items():
            destination = root / 'exports' / f'{shard:04d}.jsonl'
            temporary = destination.with_suffix('.assembling')
            counts = Counter(p['counts'])
            with temporary.open('wb') as out:
                with (root / 'prefixes' / f'{shard:04d}.jsonl.partial').open('rb') as saved:
                    shutil.copyfileobj(saved, out)
                for i, job in enumerate(jobs):
                    if job['shard'] != shard:
                        continue
                    with (root / 'segments' / f'{i:04d}.jsonl').open('rb') as stream:
                        next(stream)
                        for raw in stream:
                            row = json.loads(raw)
                            if row['kind'] == 'function':
                                counts[row['status']] += 1
                                out.write(raw)
                            elif row['kind'] == 'symbol' and job.get('symbols_only'):
                                out.write(raw)
                end = dict(kind='complete', total_enumerated=total, attempted=sum(counts.values()),
                           ok=counts['ok'], failed=counts['failed'], timeout=counts['timeout'], recovered_prefix=True)
                out.write((json.dumps(end, separators=(',', ':'))+'\n').encode())
            inspect(temporary)
            temporary.replace(destination)
            (root / 'exports' / f'{shard:04d}.jsonl.partial').unlink()
            persist(completed_shards=state['completed_shards']+1)
        if digest(snapshot) != expected['snapshot_id']:
            raise ValueError('Immutable backing changed')
        persist(state='waiting_for_index_space')
        while shutil.disk_usage(root).free < 34 * GIB:
            if stop.wait(5):
                raise RuntimeError('Cancelled before indexing')
        persist(state='indexing_checkpoint')
        coverage = build((root / 'exports').glob('*.jsonl'), root / 'corpus.sqlite',
                         {program['sha256']: program}, max_bytes=12*GIB)
        atomic_json(root / 'coverage.json', coverage)
        previous = json.loads(args.catalog.read_text())['databases']
        atomic_json(root / 'catalog.json', dict(complete=False, checkpoint_export_complete=True,
                    fixed_point_validated=False, databases=[*previous, str(root/'corpus.sqlite')],
                    snapshot_id=expected['snapshot_id']))
        persist(state='complete', complete=True, database=str(root/'corpus.sqlite'))
    except BaseException as error:
        cancel()
        persist(state='blocked_or_failed', error=str(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--source-unit', required=True)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--workers', type=int, default=42)
    args = parser.parse_args()
    if not 1 <= args.workers <= (os.cpu_count() or 1):
        parser.error('Workers must fit the host CPU count')
    run(args)
