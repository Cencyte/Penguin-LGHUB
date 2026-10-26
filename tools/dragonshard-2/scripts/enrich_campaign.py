#!/usr/bin/env python3
"""Independent, bounded FID/gap/exception jobs over verified saved analysis snapshots."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import sqlite3
import subprocess
import time

from campaign import available
from dragonshard import HERE, GIB, atomic_json, digest, locks, source_state
from swarm_tasks import evidence_summary


def ro_database(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def plan(queue_path, export):
    queue = json.loads(queue_path.read_text())
    saved = json.loads((export / 'status.json').read_text())
    if not saved.get('complete'):
        raise ValueError('Checkpoint export must be complete')
    manifest = json.loads((export / 'manifest.json').read_text())
    packed_root = Path(saved.get('source', export))
    packed = packed_root / 'checkpoint.gzf'
    if digest(packed) != manifest['snapshot_id']:
        raise ValueError('Packed checkpoint identity mismatch')
    programs = []
    for entry in queue['programs']:
        if entry['sha256'] == manifest['programs'][0]['sha256']:
            continue
        if entry['state'] != 'complete':
            raise ValueError('Only completed source programs can be enriched')
        gap = next(j for j in queue['jobs'].values() if j['kind'] == 'gaps' and
                   j['program']['sha256'] == entry['sha256'])
        source = dict(gap['manifest'], project_dir=gap['source_dir'], project_name=gap['source_name'])
        state = source_state(source)
        snapshot = hashlib.sha256(json.dumps(state, separators=(',', ':')).encode()).hexdigest()
        if locks(source) or snapshot != gap['snapshot_id']:
            raise ValueError('Source reopened/changed: ' + entry['name'])
        logical = sum(p.stat().st_size for p in Path(gap['source_dir']).rglob('*') if p.is_file())
        programs.append(dict(program=gap['program'], manifest=manifest, source_dir=gap['source_dir'],
            source_name=gap['source_name'], snapshot_id=snapshot, database=entry['database'],
            functions=entry['coverage']['recognized_internal_functions'], clone_bytes=logical))
    programs.append(dict(program=manifest['programs'][0], manifest=manifest, packed=str(packed),
        packed_cache=str(packed_root / 'packed-cache'), snapshot_id=manifest['snapshot_id'],
        database=str(export / 'corpus.sqlite'), functions=saved['functions'], clone_bytes=0))
    jobs = []
    for p in programs:
        sha = p['program']['sha256']
        n = 42 if p.get('packed') else max(1, (p['functions'] + 7999) // 8000)
        for i in range(n):
            jobs.append(dict(p, id=f'{sha[:16]}.fid.{i:03d}', kind='fid', shard=i, shards=n,
                             memory_gib=1.1, cap=64 * 1024**2, state='ready'))
        with ro_database(p['database']) as db:
            for address, status, error in db.execute(
                    "SELECT address,status,error FROM functions WHERE sha256=? AND status!='ok'", (sha,)):
                jobs.append(dict(p, id=f'{sha[:16]}.exception.{address}', kind='exception',
                    address=address, original_status=status, original_error=error,
                    memory_gib=14, cap=512 * 1024**2, state='ready'))
        if p.get('packed'):
            jobs.append(dict(p, id=f'{sha[:16]}.gaps', kind='gaps', memory_gib=2,
                             cap=512 * 1024**2, state='ready'))
    # Start large-agent FID work promptly; reserve the pilot as part of real coverage.
    jobs.sort(key=lambda j: (0 if j['kind'] == 'fid' and j.get('packed') else
                            1 if j['kind'] == 'exception' else 2 if j['kind'] == 'gaps' else 3, j['id']))
    return manifest, jobs


def validate(job, output):
    sha, snapshot = job['program']['sha256'], job['snapshot_id']
    if job['kind'] == 'exception':
        report = json.loads(output.read_text())
        if (not report.get('complete') or report['sha256'] != sha or report['snapshot_id'] != snapshot or
                [f['address'] for f in report['functions']] != [job['address']]):
            raise ValueError('Exception evidence identity/coverage mismatch')
        f = report['functions'][0]
        return dict(complete=True, standard_recovered=f['standard_retry']['completed'],
                    normalization_completed=f['normalization_attempt']['completed'],
                    instructions=len(f['instructions']))
    end = evidence_summary(output, sha, snapshot)
    if job['kind'] == 'fid':
        ordinals = set()
        with output.open() as stream:
            header = json.loads(next(stream))
            for line in stream:
                row = json.loads(line)
                if row['kind'] == 'function':
                    ordinal = row['ordinal']
                    if ordinal in ordinals:
                        raise ValueError('Duplicate FID ordinal')
                    ordinals.add(ordinal)
        expected = set(range(job['shard'], job['functions'], job['shards']))
        if (ordinals != expected or end['attempted'] != len(expected) or
                end['total_enumerated'] != job['functions'] or header['shard'] != job['shard'] or
                header['shards'] != job['shards']):
            raise ValueError('FID coverage mismatch')
    return end


def execute(job, root):
    work = root / 'tasks' / job['id']
    work.mkdir(parents=True, exist_ok=False)
    atomic_json(work / 'owner.json', dict(tool='dragonshard-2-enrichment', task=job['id']))
    (work / 'temp').mkdir()
    output = work / ('evidence.json' if job['kind'] == 'exception' else 'evidence.jsonl')
    java = [f'-Djava.io.tmpdir={work / "temp"}']
    if job.get('research_clone'):
        java.append('-Ddragonshard.research.clone=true')
    project = job.get('source_name', 'Driver')
    folder = str(PurePosixPath(job['program']['project_path']).parent).strip('/')
    clone = work / 'clone'
    clone.mkdir()
    if job.get('packed'):
        java += [f'-Ddragonshard.packed.path={job["packed"]}', f'-Dpdb.cache.dir={job["packed_cache"]}']
    else:
        source = dict(job['manifest'], project_dir=job['source_dir'], project_name=job['source_name'])
        before = source_state(source)
        if locks(source) or hashlib.sha256(json.dumps(before, separators=(',', ':')).encode()).hexdigest() != job['snapshot_id']:
            raise ValueError('Source changed before cloning')
        for suffix in ('.gpr', '.rep'):
            subprocess.run(['cp', '--reflink=auto', '-r', '--',
                str(Path(job['source_dir']) / (project + suffix)), str(clone)], check=True)
        if locks(source) or source_state(source) != before:
            raise ValueError('Source changed during cloning')
        if folder:
            project += '/' + folder
    sha = job['program']['sha256']
    if job['kind'] == 'fid':
        script = 'DragonShardFid.java'
        arguments = [output, job['shard'], job['shards'], sha, job['snapshot_id'], job['cap'], job['functions']]
    elif job['kind'] == 'gaps':
        script = 'DragonShardGaps.java'
        arguments = [output, sha, job['snapshot_id'], job['cap']]
    else:
        script = job.get('exception_script', 'DragonShardExceptionEvidence.java')
        request = work / 'request.json'
        content = dict(sha256=sha, snapshot_id=job['snapshot_id'], addresses=[job['address']],
            timeout_seconds=600, max_instructions=2000000, payload_mib=128,max_output_bytes=job['cap'])
        content.update(job.get('exception_request', {}))
        if (content['sha256'], content['snapshot_id'], content['addresses']) != (sha, job['snapshot_id'], [job['address']]):
            raise ValueError('Exception request identity/selection mismatch')
        atomic_json(request, content)
        arguments = [request, output]
    command = [str(Path(job['manifest']['ghidra']) / 'support/analyzeHeadless'), str(clone), project,
               '-noanalysis', '-max-cpu', '1', '-scriptPath', str(HERE)]
    if not job.get('packed'):
        command += ['-process', PurePosixPath(job['program']['project_path']).name, '-readOnly']
    command += ['-preScript' if job.get('packed') else '-postScript', script, *map(str, arguments)]
    with (work / 'headless.log').open('w') as log:
        p = subprocess.Popen(command, env=dict(os.environ, JAVA_TOOL_OPTIONS=' '.join(java),
            GHIDRA_HEADLESS_MAXMEM='1200m' if job['kind'] == 'exception' else '640m'),
            stdout=log, stderr=subprocess.STDOUT, start_new_session=not job.get('inherit_process_group', False))
        while p.poll() is None:
            time.sleep(1)
            if shutil.disk_usage(root).free < 22 * GIB:
                stop_worker(p, group=os.getpgid(p.pid)); raise RuntimeError('Disk reserve reached')
            if sum(f.stat().st_size for f in work.glob('evidence*') if f.is_file()) > job['cap']:
                stop_worker(p, group=os.getpgid(p.pid)); raise RuntimeError('Evidence output cap exceeded')
        if p.returncode:
            raise RuntimeError(f'Ghidra exited {p.returncode}; inspect headless.log')
    result = validate(job, output)
    atomic_json(work / 'result.json', dict(result, output=str(output), complete=True))
    # Only this task's private replica is reclaimed, after evidence validation and JVM exit.
    shutil.rmtree(clone)
    return result


def stop_worker(process, grace=10, *, group=None):
    # analyzeHeadless is a shell launcher: terminating only it can leave the JVM
    # and native decompiler writing past the disk guard. Each job owns a session.
    group = process.pid if group is None else group
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try: os.killpg(group, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()


def run(args):
    from concurrent.futures import ThreadPoolExecutor
    manifest, jobs = plan(args.queue, args.export)
    root = args.directory.resolve(); root.mkdir(parents=True, exist_ok=False)
    sage = subprocess.run([manifest['sage_python'], str(HERE / 'sage_probe.py'), '--command',
        manifest['sage_command']], check=True, capture_output=True, text=True, timeout=45)
    telemetry = json.loads(sage.stdout)
    if not telemetry.get('metrics'):
        raise RuntimeError('SystemSage did not provide metrics')
    atomic_json(root / 'systemsage.json', telemetry)
    atomic_json(root / 'owner.json', dict(tool='dragonshard-2-enrichment', pid=os.getpid()))
    atomic_json(root / 'queue.json', jobs)
    status = dict(state='validating_first_worker', complete=False, workers=args.workers, active=0,
        jobs=len(jobs), completed=0, failed=0, functions_fid_scanned=0, function_matches=0,
        exception_recoveries=0, purpose='Full selected corpus enrichment; candidate evidence is not semantic proof')
    def persist():
        atomic_json(root / 'status.json', status)
        atomic_json(root / 'queue.json', jobs)
    persist()
    # A previously running, individually validated pilot may be handed off without
    # discarding its already computed records. Its old coordinator must be stopped
    # with SIGSTOP (main process only); its Java child keeps running.
    def adopt_pilot():
        old = args.adopt_pilot / 'tasks' / jobs[0]['id']
        output = old / 'evidence.jsonl'
        while not output.exists():
            state = subprocess.check_output(['systemctl','--user','show',args.adopt_unit,'-p','ActiveState','--value'],text=True).strip()
            if state != 'active': raise RuntimeError('Adopted pilot service exited before saving evidence')
            time.sleep(2)
        result = validate(jobs[0], output)
        work = root / 'tasks' / jobs[0]['id'];work.mkdir(parents=True)
        shutil.copy2(output, work / 'evidence.jsonl')
        if digest(output) != digest(work / 'evidence.jsonl'):raise RuntimeError('Adopted evidence copy mismatch')
        atomic_json(work/'result.json',dict(result, output=str(work/'evidence.jsonl'),complete=True,adopted_from=str(old)))
        subprocess.run(['systemctl','--user','stop',args.adopt_unit],check=True)
        return result

    if args.adopt_pilot:
        if not args.adopt_unit or not args.adopt_unit.startswith('dragonshard2-enrichment-'):
            raise ValueError('An owned enrichment unit is required for pilot handoff')
        oldjob=json.loads((args.adopt_pilot/'queue.json').read_text())[0]
        if any(oldjob[k]!=jobs[0][k] for k in ('id','snapshot_id','functions','shard','shards')):
            raise ValueError('Adopted pilot does not match this inventory')
        pilot=args.adopt_pilot/'tasks'/jobs[0]['id']/'evidence.jsonl.partial'
        if not pilot.exists():pilot=pilot.with_suffix('')
        with pilot.open() as stream:
            header=json.loads(next(stream));prefix=[json.loads(next(stream)) for _ in range(100)]
        if (header['sha256']!=jobs[0]['program']['sha256'] or header['snapshot_id']!=jobs[0]['snapshot_id'] or
            [r['ordinal'] for r in prefix]!=list(range(0,100*jobs[0]['shards'],jobs[0]['shards']))):
            raise ValueError('Pilot prefix validation failed')
        jobs[0]['state']='running';status.update(state='running',active=1);persist()
    # Compile and validate one real shard before scheduling every JVM on an initial run.
    try:
        if not args.adopt_pilot:
            jobs[0]['state'] = 'running';status['active']=1;persist()
            result = execute(jobs[0], root)
            jobs[0].update(state='complete', result=result)
            status.update(completed=1, functions_fid_scanned=result['attempted'],
                          function_matches=result['matched_functions'], state='running')
            persist()
    except BaseException as error:
        jobs[0].update(state='failed', error=str(error));status.update(state='blocked_or_failed', error=str(error));persist();raise
    active = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        if args.adopt_pilot:active[pool.submit(adopt_pilot)]=jobs[0]
        while active or any(j['state'] == 'ready' for j in jobs):
            for future in list(active):
                if not future.done():
                    continue
                job = active.pop(future)
                try:
                    result = future.result(); job.update(state='complete', result=result)
                    status['completed'] += 1
                    if job['kind'] == 'fid':
                        status['functions_fid_scanned'] += result['attempted']
                        status['function_matches'] += result['matched_functions']
                    elif job['kind'] == 'exception':
                        status['exception_recoveries'] += int(result['standard_recovered'])
                except Exception as error:
                    job.update(state='failed', error=str(error)); status['failed'] += 1
            # Budget peak worker memory as well as live MemAvailable; reserve space for the final analysis producer.
            running = list(active.values())
            ram = sum(j['memory_gib'] for j in running)
            disk = sum(j['clone_bytes'] + j['cap'] for j in running)
            for job in jobs:
                if job['state'] != 'ready' or len(active) >= args.workers:
                    continue
                if (ram + job['memory_gib'] > args.memory_gib or available() < job['memory_gib'] + 10 or
                    shutil.disk_usage(root).free < 32 * GIB + disk + job['clone_bytes'] + job['cap']):
                    continue
                job['state'] = 'running'; active[pool.submit(execute, job, root)] = job
                ram += job['memory_gib']; disk += job['clone_bytes'] + job['cap']
            status.update(active=len(active), ready=sum(j['state'] == 'ready' for j in jobs),
                          reserved_worker_memory_gib=ram, available_memory_gib=available())
            persist(); time.sleep(2)
    status.update(state='complete' if not status['failed'] else 'blocked_or_failed', complete=not status['failed'], active=0)
    persist()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('queue', type=Path);parser.add_argument('export', type=Path);parser.add_argument('directory', type=Path)
    parser.add_argument('--workers', type=int, default=42);parser.add_argument('--memory-gib', type=float, default=48)
    parser.add_argument('--adopt-pilot', type=Path);parser.add_argument('--adopt-unit')
    args = parser.parse_args()
    if not 1 <= args.workers <= (os.cpu_count() or 1) or args.memory_gib < 6:
        parser.error('Invalid resource limits')
    run(args)
