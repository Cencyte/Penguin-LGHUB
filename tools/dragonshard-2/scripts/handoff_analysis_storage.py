#!/usr/bin/env python3
"""Move a running analysis at its next closed finish checkpoint without discarding it."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from complete_storage_move import has_java, launch, migrate_hold, prop
from dragonshard import atomic_json, locks
from relocate_campaign import relocate


def load(path):
    return json.loads(Path(path).read_text())


def validate_checkpoint(path, sha256):
    report = load(path / 'report.json')
    if report.get('mode') != 'finish' or report.get('sha256') != sha256 or not report.get('complete'):
        raise RuntimeError('Finish checkpoint is incomplete or belongs to a different binary')
    if not (path / 'program.gzf').is_file() or not (path / 'functions.json').is_file():
        raise RuntimeError('Finish checkpoint is missing its database or function inventory')
    source = dict(project_dir=report['source_dir'], project_name=report['source_name'])
    if locks(source):
        raise RuntimeError('Finish checkpoint project is still locked')
    return report


def coordinator_identity(pid, source):
    proc = Path('/proc') / str(pid)
    command = (proc / 'cmdline').read_bytes().split(b'\0')
    if not any(arg.endswith(b'/parallel_analysis.py') for arg in command) or os.fsencode(source) not in command:
        raise RuntimeError('Analysis coordinator identity changed')
    # The kernel start tick protects against reusing a PID during a long handoff.
    return (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]


def wait_started(unit, root):
    for _ in range(60):
        if (root / 'status.json').exists():
            state = load(root / 'status.json')
            if state.get('state') == 'blocked_or_failed':
                raise RuntimeError('Replacement analysis failed: ' + str(state.get('error')))
            if prop(unit, 'ActiveState') in ('active', 'activating'):
                return
        time.sleep(1)
    raise RuntimeError('Replacement analysis did not publish a live status')


def release_parent(config, state_path):
    """ExecStopPost backstop: never leave the original controller SIGSTOPped."""
    if not state_path.exists():
        return
    state = load(state_path)
    if state.get('original_retired') or state.get('state') == 'complete':
        return
    pid = state.get('coordinator_pid')
    if not pid or prop(config['source_unit'], 'MainPID') != str(pid):
        return
    try:
        if coordinator_identity(pid, Path(config['source'])) == state['coordinator_start_tick']:
            os.kill(pid, signal.SIGCONT)
            if state.get('source_freeze_owned'):
                subprocess.run(['systemctl', '--user', 'thaw', config['source_unit']], check=False)
    except (FileNotFoundError, ProcessLookupError):
        pass


def handoff(config, state_path):
    source, target = Path(config['source']), Path(config['destination'])
    campaign, repo = Path(config['campaign']), Path(config['repository'])
    checkpoint = source / config['checkpoint']
    unit, new_unit = config['source_unit'], config['new_unit']
    new_root = Path(config['new_root'])
    if not all(u.startswith('dragonshard2-') for u in (unit, new_unit)):
        raise RuntimeError('Only explicitly owned DragonShard units may be handed off')
    if source.is_symlink() or not source.is_dir() or target.exists() or new_root.exists():
        raise RuntimeError('Source must be a real directory and destinations must be unused')
    if target.parent.stat().st_dev != Path('/home/firesongz').stat().st_dev:
        raise RuntimeError('Requested work destination is not on the system filesystem')
    if load(source / 'owner.json').get('tool') != 'dragonshard-2-parallel-analysis':
        raise RuntimeError('Source is not an owned analysis run')
    sha = load(source / 'manifest.json')['programs'][0]['sha256']
    pid = int(prop(unit, 'MainPID'))
    identity = coordinator_identity(pid, source)
    suspended, retired, notifier_stopped, queue_frozen = False, False, False, False
    source_frozen = False
    replacement_ready = False

    def record(state, **fields):
        atomic_json(state_path, dict(at=time.time(), state=state, source=str(source),
            destination=str(target), checkpoint=str(checkpoint), source_unit=unit,
            new_unit=new_unit, coordinator_pid=pid, coordinator_start_tick=identity,
            original_retired=retired, source_freeze_owned=source_frozen, **fields))

    try:
        # Suspend ONLY the Python parent. Its Ghidra child keeps computing and
        # saving; the parent cannot launch the next round on the old filesystem.
        record('suspending_coordinator_only')
        os.kill(pid, signal.SIGSTOP)
        suspended = True
        for _ in range(100):
            if (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'T':
                break
            time.sleep(.01)
        else:
            raise RuntimeError('Coordinator did not stop at the handoff boundary')
        current = load(source / 'status.json')
        expected_checkpoint = f"finish-{int(current.get('round', -1)):02d}"
        if current.get('state') != 'remaining_analyzers' or checkpoint.name != expected_checkpoint:
            raise RuntimeError('Current analysis phase changed; no live files moved')
        helper = config.get('scratch_unit')
        if helper:
            if not helper.startswith('dragonshard2-scratch-'):
                raise RuntimeError('Unexpected scratch helper unit')
            subprocess.run(['systemctl', '--user', 'stop', helper], check=True)
        # Move closed files now. Live project/scratch/cache inodes stay on their
        # original filesystem behind aliases until the child saves and exits.
        # Freeze for the path handoff; never copy an actively writable database.
        if prop(unit, 'FreezerState') != 'running':
            raise RuntimeError('Producer already frozen by another owner')
        source_frozen = True
        record('freezing_for_path_handoff')
        subprocess.run(['systemctl', '--user', 'freeze', unit], check=True)
        held = source.with_name(source.name + '-live-hold-for-zfs')
        record('moving_closed_files_preserving_live_inodes', held_root=str(held))
        relocate(source, target, held, [Path(checkpoint.name), Path('packed-cache')], [unit])
        subprocess.run(['systemctl', '--user', 'thaw', unit], check=True)
        source_frozen = False
        while True:
            if coordinator_identity(pid, source) != identity:
                raise RuntimeError('Original coordinator PID was reused')
            report = load(checkpoint / 'report.json')
            computing = has_java(unit)
            record('waiting_for_closed_checkpoint', checkpoint_complete=bool(report.get('complete')),
                   ghidra_running=computing)
            if report.get('complete') and not computing:
                validate_checkpoint(checkpoint, sha)
                break
            if report.get('error') or not computing:
                raise RuntimeError('Current analyzer exited without a complete checkpoint')
            time.sleep(5)

        notification_unit = config.get('notification_unit')
        if notification_unit and prop(notification_unit, 'ActiveState') == 'active':
            subprocess.run(['systemctl', '--user', 'stop', notification_unit], check=True)
            notifier_stopped = True
        queue_unit = config['queue_unit']
        if not queue_unit.startswith('dragonshard2-workqueue-') or load(Path(config['queue_root']) / 'status.json')['active_tasks']:
            raise RuntimeError('Export queue must be owned and idle during producer replacement')
        subprocess.run(['systemctl', '--user', 'freeze', queue_unit], check=True)
        queue_frozen = True
        # Ghidra has exited and saved. Retire its stopped parent without giving it
        # an opportunity to launch another task; no unsaved program is killed.
        os.kill(pid, signal.SIGKILL)
        retired = True
        record('retiring_closed_producer')
        while prop(unit, 'ActiveState') in ('active', 'activating', 'deactivating'):
            time.sleep(1)
        record('copying_verified_closed_databases')
        for relative in [checkpoint.name, 'packed-cache']:
            migrate_hold(held / relative, target / relative)
        validate_checkpoint(target / checkpoint.name, sha)
        record('starting_zfs_analysis')
        scripts = repo / 'tools/dragonshard-2/scripts'
        launch(new_unit, ['/usr/bin/python3', str(scripts / 'parallel_analysis.py'),
            str(campaign / 'batch4/run/manifest.json'), str(new_root), '--workers', str(config.get('workers', 42)),
            '--checkpoint', str(target / checkpoint.name), '--finish-auto-analysis',
            '--disk-reserve-gib', str(config.get('disk_reserve_gib', 22))], repo, '60G', '66G')
        wait_started(new_unit, new_root)
        atomic_json(campaign / 'depth-v2/parallel-analysis.json', dict(directory=str(new_root), unit=new_unit))
        replacement_ready = True
        record('complete', new_root=str(new_root), old_paths_preserved=True,
               completed_checkpoint_transferred=True)
    except BaseException as error:
        record('blocked_or_failed', error=str(error))
        if suspended and not retired:
            try:
                if coordinator_identity(pid, source) == identity:
                    os.kill(pid, signal.SIGCONT)
            except (FileNotFoundError, ProcessLookupError):
                pass
        raise
    finally:
        if source_frozen and not retired:
            subprocess.run(['systemctl', '--user', 'thaw', unit], check=False)
        if queue_frozen and (replacement_ready or not retired):
            subprocess.run(['systemctl', '--user', 'thaw', config['queue_unit']], check=False)
        if notifier_stopped:
            subprocess.run(['systemctl', '--user', 'start', config['notification_unit']], check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('state', type=Path)
    parser.add_argument('--release-parent', action='store_true')
    args = parser.parse_args()
    if args.release_parent:
        release_parent(load(args.config), args.state)
        return
    def interrupted(*_):
        raise RuntimeError('Storage handoff interrupted; saved evidence retained')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    handoff(load(args.config), args.state)


if __name__ == '__main__':
    main()
