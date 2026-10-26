#!/usr/bin/env python3
"""Resume a stopped queue whose sole failure was the symbol-heavy first export shard."""
import argparse
import fcntl
import json
from pathlib import Path
import shutil
import signal
import subprocess

from corpus import inspect
from dragonshard import GIB, atomic_json, digest
from swarm import Swarm


def select_retry(queue):
    jobs = queue['jobs']
    failed = [j for j in jobs.values() if j['state'] == 'failed']
    if len(failed) != 1 or any(j['state'] not in ('complete', 'failed', 'pending') for j in jobs.values()):
        raise ValueError('Recovery requires exactly one failure and no active tasks')
    job = failed[0]
    pending = [j for j in jobs.values() if j['state'] == 'pending']
    if (job['kind'] != 'export' or job.get('shard') != 0 or not job.get('packed_file') or
        len(pending) != 1 or pending[0]['kind'] != 'index' or
        pending[0]['program']['sha256'] != job['program']['sha256'] or job['id'] not in pending[0]['deps']):
        raise ValueError('Only a packed shard-zero export and its dependent index may need work')
    if any(p['state'] not in ('complete', 'exporting') for p in queue['programs']):
        raise ValueError('Other program analysis is unfinished')
    return job


def run(args):
    root = args.directory.resolve()
    if not args.source_unit.startswith('dragonshard2-'):
        raise ValueError('Explicit owned source unit required')
    state = subprocess.check_output(['systemctl', '--user', 'show', args.source_unit,
                                    '-p', 'ActiveState', '--value'], text=True).strip()
    if state not in ('inactive', 'failed'):
        raise ValueError('Source coordinator is still active')
    if json.loads((root / 'owner.json').read_text()).get('tool') != 'dragonshard-2-swarm':
        raise ValueError('Not an owned DragonShard queue')
    lock = (root / 'coordinator.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    queue = json.loads((root / 'queue.json').read_text())
    job = select_retry(queue)
    log = root / 'logs' / (job['id'] + '.log')
    if 'Shard output budget exhausted' not in log.read_text():
        raise ValueError('Failure was not the recorded output cap')
    manifest = json.loads((root / 'manifest.json').read_text())
    status = json.loads((root / 'status.json').read_text())
    swarm = Swarm(manifest, root, cpu_limit=status['cpu_slots_limit'],
                  disk_reserve_gib=status['disk_reserve_gib'])
    swarm.sage()
    if digest(job['packed_file']) != job['snapshot_id'] or digest(job['program']['binary_path']) != job['program']['sha256']:
        raise ValueError('Analysis snapshot or executable changed')
    siblings = [j for j in queue['jobs'].values() if j['kind'] == 'export' and
                j['program']['sha256'] == job['program']['sha256'] and j['id'] != job['id']]
    if len(siblings) != job['shards'] - 1 or any(j['state'] != 'complete' for j in siblings):
        raise ValueError('Other export partitions are not complete')
    for sibling in siblings:
        header, end = inspect(Path(sibling['result']['output']))
        for field in ('snapshot_id', 'shard', 'shards'):
            if header[field] != sibling[field]:
                raise ValueError('Saved export partition identity mismatch')
        if header['sha256'] != job['program']['sha256'] or end['attempted'] != sibling['result']['attempted']:
            raise ValueError('Saved export coverage mismatch')
    partial = Path(job['work']) / 'exports' / f"0000-of-{job['shards']:04d}.jsonl.partial"
    if not partial.is_file() or partial.with_suffix('').exists():
        raise ValueError('Failed shard does not have exactly one incomplete output')
    with partial.open() as stream:
        header = json.loads(next(stream))
    if (header['sha256'], header['snapshot_id'], header['shard']) != (job['program']['sha256'], job['snapshot_id'], 0):
        raise ValueError('Partial evidence identity mismatch')
    total_budget = swarm.program_budget(dict(program=job['program'])) * GIB
    cap = int(total_budget * .4 * .25 + total_budget * .4 * .75 / job['shards'])
    if cap <= job['output_limit_bytes']:
        raise ValueError('Replacement cap does not increase the failed allowance')
    if shutil.disk_usage(root).free < (status['disk_reserve_gib'] * GIB + total_budget + 2 * GIB):
        raise ValueError('Insufficient space for bounded export/index recovery')
    archive = root / 'symbol-export-retry-v1'
    archive.mkdir(exist_ok=False)
    for name in ('queue.json', 'status.json', 'catalog.json'):
        shutil.copy2(root / name, archive / name)
    clone = Path(job['work']) / 'clones' / '0000'
    if clone.exists():
        owner = json.loads((clone / 'owner.json').read_text())
        if clone.is_symlink() or owner['task'] != job['id']:
            raise ValueError('Unowned failed worker project')
        clone.rename(archive / 'failed-clone')
    partial.rename(archive / partial.name)
    log.rename(archive / log.name)
    atomic_json(archive / 'retry.json', dict(task=job['id'], old_limit=job['output_limit_bytes'],
        new_limit=cap, retained_complete_shards=len(siblings), source_unit=args.source_unit))
    job.update(state='pending', output_limit_bytes=cap, retry_of=str(archive))
    for field in ('pid', 'returncode', 'started_at'):
        job.pop(field, None)
    swarm.jobs = queue['jobs']
    swarm.programs = {p['sha256']: p for p in queue['programs']}
    swarm.imported_databases = json.loads((root / 'catalog.json').read_text())['databases']
    swarm.queue_version += 1
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: setattr(swarm, 'stop', True))
    swarm.run(planned=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--source-unit', required=True)
    run(parser.parse_args())
