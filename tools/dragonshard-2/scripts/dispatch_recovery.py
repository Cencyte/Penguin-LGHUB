#!/usr/bin/env python3
"""Recover recorded dispatch gaps on independent, budgeted research copies."""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from campaign import available
from dragonshard import HERE, GIB, atomic_json
from enrich_campaign import execute as recover_exception, stop_worker
from swarm import process_group_memory, cgroup_memory_headroom
from swarm_tasks import execute


def plan(queue, inventory, root):
    sources = {j['program']['sha256']: j for j in queue['jobs'].values() if j['kind'] == 'indirect'}
    grouped = defaultdict(list)
    for row in inventory['unmatched']:
        grouped[row['sha256']].append({'address': row['function'], 'sites': row['sites']})
    jobs = []
    raw = defaultdict(list)
    for row in inventory.get('raw_sites', []): raw[row['sha256']].append(row['address'])
    for sha, sites in sorted(raw.items()):
        for i in range(0,len(sites),16):
            task=dict(sources[sha],id=f'{sha[:16]}.context.{i//16:03d}',functions=[],raw_sites=sites[i:i+16],
                      state='ready',output_limit_bytes=64*1024**2,memory_gib=1.5)
            task.pop('result',None);task['work']=str(root/'audit'/task['id']);jobs.append(task)
    for sha, functions in sorted(grouped.items()):
        source = sources[sha]
        for i in range(0, len(functions), 8):
            task = dict(source, id=f'{sha[:16]}.audit.{i//8:03d}',
                        functions=functions[i:i+8], audit_requested_sites=True,
                        state='ready', output_limit_bytes=64*1024**2)
            task.pop('result', None)
            task['work'] = str(root/'audit'/task['id'])
            task['memory_gib'] = 3 if task.get('packed_file') else 1.5
            jobs.append(task)
    zfs = root.parent
    for failure in inventory['failed']:
        sha = failure['sha256']; source = sources[sha]
        switch = bool(source.get('packed_file'))
        prior = (zfs/'switch-recovery-zfs-v1'/failure['address']/'request.json' if switch else
                 zfs/'fragment-recovery-zfs-v1'/sha[:16]/'request.json')
        request = json.loads(prior.read_text())
        request.update(snapshot_id=source['snapshot_id'], indirect_sites=failure['requested_sites'])
        task = dict(source, id=f'{sha[:16]}.recovery.{failure["address"]}', kind='exception',
                    address=request['addresses'][0], original_address=failure['address'],
                    exception_script='DragonShardSwitchRecovery.java' if switch else 'DragonShardFragmentRecovery.java',
                    exception_request=request, research_clone=True, inherit_process_group=True, cap=64*1024**2,
                    state='ready', memory_gib=3)
        task.pop('result', None)
        if switch:
            task.update(packed=source['packed_file'])
        jobs.insert(0, task)
    sizes = {}
    for job in jobs:
        source = job['source_dir']
        if source not in sizes:
            sizes[source] = sum(p.stat().st_size for p in Path(source).rglob('*') if p.is_file())/GIB
        job['disk_gib'] = .125 + (0 if job.get('packed_file') else sizes[source])
    return jobs


def run(args):
    root = args.directory.resolve(); root.mkdir(parents=True, exist_ok=False)
    jobs = plan(json.loads(args.queue.read_text()), json.loads(args.inventory.read_text()), root)
    for job in jobs:
        job.update(recovery_style=args.style, retain_unreachable=args.retain_unreachable)
    atomic_json(root/'owner.json', {'tool': 'dragonshard-2-dispatch-recovery', 'pid': os.getpid()})
    atomic_json(root/'queue.json', jobs)
    active = {}; stopped = False; last_sage = 0; peak = 0

    def cancel(*_):
        nonlocal stopped
        stopped = True
        for proc, _, log in list(active.values()):
            stop_worker(proc); log.close()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, cancel)
    try:
        while any(j['state'] in ('ready', 'running') for j in jobs):
            if stopped:
                raise RuntimeError('Cancelled; partial evidence retained')
            if shutil.disk_usage(root).free < 22*GIB or shutil.disk_usage(args.inventory.parent).free < 16*GIB:
                raise RuntimeError('Disk reserve reached; partial evidence retained')
            if time.monotonic()-last_sage > 60:
                m = jobs[0]['manifest']
                sage = subprocess.run([m['sage_python'], str(HERE/'sage_probe.py'), '--command',
                                       m['sage_command']], check=True, capture_output=True, text=True, timeout=45)
                telemetry = json.loads(sage.stdout)
                if not telemetry.get('metrics'):
                    raise RuntimeError('SystemSage returned no metrics')
                atomic_json(root/'systemsage.json', telemetry); last_sage = time.monotonic()
            for name, (proc, job, log) in list(active.items()):
                if proc.poll() is None:
                    continue
                log.close(); del active[name]
                result = root/'jobs'/f'{name}.result.json'
                if proc.returncode == 0 and result.is_file():
                    job.update(state='complete', result=json.loads(result.read_text()))
                else:
                    job.update(state='failed', error=f'Worker exit {proc.returncode}; see logs/{name}.log')
            usage = process_group_memory([p.pid for p, _, _ in active.values()])
            pending_ram = sum(max(0, j['memory_gib']-usage.get(p.pid, 0)) for p,j,_ in active.values())
            ram = min(available()-8-pending_ram, cgroup_memory_headroom()-pending_ram)
            disk = shutil.disk_usage(root).free/GIB-22-sum(j['disk_gib'] for _,j,_ in active.values())
            for job in jobs:
                if job['state'] != 'ready' or len(active) >= args.workers:
                    continue
                if job['memory_gib'] > ram or job['disk_gib'] > disk:
                    continue
                name = job['id']; jobfile = root/'jobs'/f'{name}.json'
                jobfile.parent.mkdir(exist_ok=True); (root/'logs').mkdir(exist_ok=True)
                atomic_json(jobfile, job)
                log = (root/'logs'/f'{name}.log').open('w')
                proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'worker',
                                         str(jobfile), str(root)], stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
                active[name] = proc, job, log
                job.update(state='running', pid=proc.pid)
                ram -= job['memory_gib']; disk -= job['disk_gib']
            peak = max(peak, len(active))
            atomic_json(root/'queue.json', jobs)
            atomic_json(root/'status.json', {'state': 'running', 'complete': False,
                'jobs': len(jobs), 'completed': sum(j['state']=='complete' for j in jobs),
                'failed': sum(j['state']=='failed' for j in jobs), 'active': len(active),
                'ready': sum(j['state']=='ready' for j in jobs), 'worker_ceiling': args.workers,
                'peak_workers': peak, 'ram_admission_remaining_gib': ram,
                'disk_admission_remaining_gib': disk})
            if active or any(j['state']=='ready' for j in jobs):
                time.sleep(1)
        complete = all(j['state']=='complete' for j in jobs)
        atomic_json(root/'status.json', {'state': 'complete' if complete else 'failed',
            'complete': complete, 'jobs': len(jobs), 'completed': sum(j['state']=='complete' for j in jobs),
            'failed': sum(j['state']=='failed' for j in jobs), 'peak_workers': peak})
    except BaseException as error:
        cancel()
        atomic_json(root/'status.json', {'state': 'stopped', 'complete': False, 'error': str(error)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    launch = sub.add_parser('run')
    launch.add_argument('queue', type=Path); launch.add_argument('inventory', type=Path)
    launch.add_argument('directory', type=Path); launch.add_argument('--workers', type=int, default=42, choices=range(1,43))
    launch.add_argument('--style', choices=('normalize', 'firstpass', 'register'), default='normalize')
    launch.add_argument('--retain-unreachable', action='store_true')
    worker = sub.add_parser('worker')
    worker.add_argument('job', type=Path); worker.add_argument('root', type=Path)
    args = parser.parse_args()
    if args.command == 'run':
        run(args)
    else:
        job = json.loads(args.job.read_text())
        result = recover_exception(job, args.root) if job['kind']=='exception' else execute(job)
        atomic_json(args.job.with_suffix('.result.json'), dict(result, complete=True))


if __name__ == '__main__':
    main()
