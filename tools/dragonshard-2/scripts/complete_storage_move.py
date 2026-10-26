#!/usr/bin/env python3
"""Close the retained merge safely, relocate live holds, and resume on the destination drive."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from dragonshard import atomic_json, digest
from relocate_campaign import relocate


def prop(unit, name):
    return subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', name, '--value'], text=True).strip()


def has_java(unit):
    group = prop(unit, 'ControlGroup')
    if not group:
        return False
    for path in (Path('/sys/fs/cgroup') / group.lstrip('/')).rglob('cgroup.procs'):
        for pid in path.read_text().split():
            try:
                if (Path('/proc') / pid / 'comm').read_text().strip() == 'java': return True
            except FileNotFoundError:
                pass
    return False


def migrate_hold(source, target):
    if not target.is_symlink() or target.resolve() != source.resolve():
        raise RuntimeError('Live hold alias does not match the owned source')
    target.unlink()
    if source.is_dir():
        relocate(source, target, source.with_name(source.name + '.empty-relocation-hold'), [], [])
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.relocating')
        shutil.copy2(source, temporary)
        with temporary.open('rb') as stream: os.fsync(stream.fileno())
        if digest(source) != digest(temporary): raise RuntimeError('Held log checksum mismatch')
        temporary.rename(target); source.unlink(); source.symlink_to(target)


def launch(unit, command, root, memory_high, memory_max, after=None):
    cmd = ['systemd-run', '--user', '--unit=' + unit, '--property=WorkingDirectory=' + str(root),
           '--property=MemoryHigh=' + memory_high, '--property=MemoryMax=' + memory_max,
           '--property=MemorySwapMax=0']
    if after: cmd += ['--property=ExecStopPost=' + after]
    subprocess.run(cmd + command, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('repository', type=Path); ap.add_argument('campaign', type=Path); ap.add_argument('held_campaign', type=Path)
    args = ap.parse_args()
    repo, campaign, held = args.repository, args.campaign, args.held_campaign
    status = campaign.parent / 'storage-handoff.json'
    old = 'dragonshard2-parallel-pipeline-v3-20261007.service'
    ahead = 'dragonshard2-agent-ahead-20261006.service'
    queue = 'dragonshard2-workqueue-v6-20261007.service'
    for unit in (old, ahead, queue):
        if not unit.startswith('dragonshard2-'): raise RuntimeError('Unowned unit')
    try:
        atomic_json(status, dict(state='waiting_for_saved_merge', campaign=str(campaign)))
        report_path = campaign / 'parallel-pipeline-v3/round-00/merge/report.json'
        while True:
            report = json.loads(report_path.read_text())
            if report.get('error'): raise RuntimeError('Retained merge failed: ' + report['error'])
            if report.get('complete') and not has_java(old): break
            time.sleep(2)
        # The Java producer has closed all databases; its controller was deliberately
        # SIGSTOPped before this boundary. No unsaved program is terminated here.
        pid = int(prop(old, 'MainPID'))
        command = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
        if b'parallel_analysis.py' not in command or b'parallel-pipeline-v3' not in command:
            raise RuntimeError('Old coordinator identity changed')
        os.kill(pid, signal.SIGKILL)
        while prop(old, 'ActiveState') in ('active', 'activating', 'deactivating'): time.sleep(1)
        subprocess.run(['systemctl', '--user', 'freeze', ahead], check=True)
        # The queue has no active computation; preserve/adopt its persisted results.
        current = json.loads((campaign / 'swarm-v6/status.json').read_text())
        if current.get('active_tasks') != 0: raise RuntimeError('Unexpected active legacy queue task')
        subprocess.run(['systemctl', '--user', 'stop', queue], check=True)
        atomic_json(status, dict(state='moving_closed_working_files', campaign=str(campaign)))
        # These are unpackings of retained immutable inputs, never the sole evidence.
        for cache in [held / 'parallel-pipeline-v3/packed-cache', held / 'parallel-pipeline-v3/round-00/merge/temp']:
            if cache.is_symlink(): raise RuntimeError('Unexpected cache symlink')
            if cache.exists(): shutil.rmtree(cache)
        for relative in ['parallel-pipeline-v3', 'swarm-v6', 'swarm-v6.log']:
            migrate_hold(held / relative, campaign / relative)
        scripts = repo / 'tools/dragonshard-2/scripts'
        next_queue = campaign / 'swarm-captures-v7'
        next_parallel = campaign / 'parallel-captures-v4'
        queue_unit = 'dragonshard2-workqueue-captures-v7-20261007.service'
        parallel_unit = 'dragonshard2-parallel-captures-v4-20261007.service'
        launch(queue_unit, ['/usr/bin/python3', str(scripts / 'swarm.py'), str(campaign / 'campaign.json'),
               str(next_queue), '--adopt-depth', str(campaign / 'depth-v2'), '--adopt-queue', str(campaign / 'swarm-v6'),
               '--cpu-slots', '48', '--disk-reserve-gib', '16'], repo, '54G', '64G')
        atomic_json(campaign / 'depth-v2/swarm.json', dict(directory=str(next_queue), unit=queue_unit))
        launch(parallel_unit, ['/usr/bin/python3', str(scripts / 'parallel_analysis.py'), str(campaign / 'batch4/run/manifest.json'),
               str(next_parallel), '--workers', '42', '--checkpoint', str(campaign / 'parallel-seed-v1/checkpoint'),
               '--resume-workers', str(campaign / 'parallel-analysis-v2'),
               '--resume-merge', str(campaign / 'parallel-pipeline-v3/round-00/merge'),
               '--finish-auto-analysis', '--pause-unit', ahead, '--pause-unit', queue_unit, '--leave-paused-unit', ahead], repo, '52G', '56G',
               '/usr/bin/systemctl --user thaw ' + queue_unit)
        atomic_json(campaign / 'depth-v2/parallel-analysis.json', dict(directory=str(next_parallel), unit=parallel_unit))
        atomic_json(status, dict(state='running_on_captures', campaign=str(campaign), queue=queue_unit,
                    parallel=parallel_unit, temporary_retained_fallback=str(held / 'depth-v2/ahead')))
        # Preserve the original analysis in RAM until a validated replacement supersedes it.
        # Its final private files can move after the global queue closes that service.
        while prop(ahead, 'ActiveState') in ('active', 'activating', 'deactivating') or has_java(ahead): time.sleep(10)
        for relative in ['depth-v2/ahead', 'depth-v2/ahead.log']:
            migrate_hold(held / relative, campaign / relative)
        atomic_json(status, dict(state='complete', campaign=str(campaign), queue=queue_unit, parallel=parallel_unit))
    except BaseException as error:
        atomic_json(status, dict(state='blocked_or_failed', error=str(error), campaign=str(campaign)))
        raise


if __name__ == '__main__':
    main()
