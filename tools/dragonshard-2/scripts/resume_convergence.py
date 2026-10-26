#!/usr/bin/env python3
"""Follow one owned analysis run, then remerge its saved final round with a reviewed width policy."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from dragonshard import atomic_json, digest
from packed_export import validate_checkpoint
from parallel_analysis import load_width_policy

NONCONVERGED = 'Analysis has not converged after four reconciliation rounds; reference run retained'


def recovery_checkpoint(source, program):
    owner = json.loads((source / 'owner.json').read_text())
    state = json.loads((source / 'status.json').read_text())
    if owner.get('tool') != 'dragonshard-2-parallel-analysis':
        raise ValueError('Not an owned parallel-analysis run')
    if state.get('state') != 'blocked_or_failed' or state.get('error') != NONCONVERGED:
        raise ValueError('Source did not stop at the expected convergence gate')
    checkpoint = source / 'finish-04'
    validate_checkpoint(checkpoint, program)
    return checkpoint


def service_state(unit):
    return subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--value'], text=True).strip()


def run(args):
    for unit in (args.source_unit, args.unit):
        if not unit.startswith('dragonshard2-'):
            raise ValueError('Only explicitly owned DragonShard services may be followed')
    if args.source_unit == args.unit:
        raise ValueError('Source and recovery units must differ')
    root = args.tracking.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source, target = args.source.resolve(), args.target.resolve()
    manifest = json.loads((source / 'manifest.json').read_text())
    if len(manifest['programs']) != 1:
        raise ValueError('Recovery requires a single-program manifest')
    program = manifest['programs'][0]
    load_width_policy(args.policy, program['sha256'])
    policy_hash = digest(args.policy)
    pointer = json.loads(args.pointer.read_text())
    if Path(pointer['directory']).resolve() != source or pointer['unit'] != args.source_unit:
        raise ValueError('Analysis pointer no longer selects this source')
    if target.exists():
        raise ValueError('Recovery target already exists')
    state = dict(state='waiting_for_final_saved_round', complete=False, source=str(source),
                 target=str(target), source_unit=args.source_unit, unit=args.unit)
    atomic_json(root / 'status.json', state)
    # Keep the export queue attached to a live owned producer across the handoff.
    # The source project and progress remain visible while it finishes its round.
    atomic_json(args.pointer, dict(pointer, unit=args.unit, analysis_unit=args.source_unit,
                                  convergence_recovery=str(root / 'status.json')))
    child = None
    try:
        while service_state(args.source_unit) in ('active', 'activating', 'deactivating'):
            prior = json.loads((source / 'status.json').read_text())
            state.update(source_state=prior.get('state'), source_round=prior.get('round'))
            atomic_json(root / 'status.json', state)
            time.sleep(5)
        prior = json.loads((source / 'status.json').read_text())
        if prior.get('complete') and (source / 'complete-analysis.json').exists():
            atomic_json(args.pointer, pointer)
            state.update(state='source_converged_without_recovery', complete=True)
            atomic_json(root / 'status.json', state)
            return
        checkpoint = recovery_checkpoint(source, program)
        if digest(args.policy) != policy_hash:
            raise ValueError('Reviewed policy changed while waiting')
        command = [sys.executable, str(Path(__file__).with_name('parallel_analysis.py')), str(source / 'manifest.json'),
                   str(target), '--workers', str(args.workers), '--checkpoint', str(checkpoint),
                   '--resume-workers', str(source), '--resume-round', 'round-04', '--finish-auto-analysis',
                   '--width-policy', str(args.policy.resolve()), '--disk-reserve-gib', '22']
        atomic_json(root / 'command.json', command)
        child = subprocess.Popen(command)
        state.update(state='remerging_saved_final_round', child_pid=child.pid)
        atomic_json(root / 'status.json', state)
        while child.poll() is None:
            if (target / 'owner.json').exists() and (target / 'manifest.json').exists():
                atomic_json(args.pointer, dict(directory=str(target), unit=args.unit,
                                              convergence_recovery=str(root / 'status.json')))
                break
            time.sleep(0.2)
        if child.wait() != 0:
            raise RuntimeError('Recovery failed; retain source and recovery evidence')
        result = json.loads((target / 'status.json').read_text())
        if not result.get('complete') or not result.get('full_auto_analysis_complete'):
            raise RuntimeError('Recovery did not validate convergence')
        state.update(state='complete', complete=True, result=str(target / 'complete-analysis.json'))
        atomic_json(root / 'status.json', state)
    except BaseException as error:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait()
        state.update(state='blocked_or_failed', error=str(error))
        atomic_json(root / 'status.json', state)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'target', 'tracking', 'pointer', 'policy'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--source-unit', required=True)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--workers', type=int, default=42)
    args = parser.parse_args()
    if not 1 <= args.workers <= (os.cpu_count() or 1):
        parser.error('Invalid worker count')
    run(args)
