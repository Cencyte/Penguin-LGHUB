#!/usr/bin/env python3
"""Analyze a large later target early, with its own resource guards and isolated project."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import time
from dragonshard import GIB, HERE, atomic_json, locks, source_state
from campaign import available


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('campaign', type=Path)
    ap.add_argument('batch', type=int)
    ap.add_argument('sha256')
    args = ap.parse_args()
    original = args.campaign.resolve()
    manifest = json.loads((original / f'batch{args.batch}/run/manifest.json').read_text())
    program = next(p for p in manifest['programs'] if p['sha256'] == args.sha256)
    root = original / 'depth-v2/ahead' / args.sha256[:16]
    root.mkdir(parents=True, exist_ok=False)
    atomic_json(root / 'owner.json', {'tool': 'dragonshard-2-ahead', 'program': program, 'batch': args.batch})
    sage = subprocess.run([manifest['sage_python'], str(HERE / 'sage_probe.py'), '--command', manifest['sage_command']], capture_output=True, text=True, timeout=45, check=True)
    atomic_json(root / 'systemsage.json', json.loads(sage.stdout))
    if available() < 24 or shutil.disk_usage(root).free < 12 * GIB:
        raise RuntimeError('Insufficient reserve for 12 GiB heap plus native memory and headroom')
    before = source_state(manifest)
    if locks(manifest):
        raise RuntimeError('Baseline source is locked')
    for suffix in ('.gpr', '.rep'):
        subprocess.run(['cp', '--reflink=auto', '-r', '--', str(Path(manifest['project_dir']) / (manifest['project_name'] + suffix)), str(root)], check=True)
    if before != source_state(manifest) or locks(manifest):
        raise RuntimeError('Baseline changed during clone')
    folder = str(PurePosixPath(program['project_path']).parent).strip('/')
    project = manifest['project_name'] + ('/' + folder if folder else '')
    command = [str(Path(manifest['ghidra']) / 'support/analyzeHeadless'), str(root), project,
               '-process', PurePosixPath(program['project_path']).name, '-noanalysis', '-max-cpu', '8',
               '-scriptPath', str(HERE), '-postScript', 'DragonShardDeepen.java', str(root / 'depth.json'), args.sha256, 'analyze']
    with (root / 'analysis.log').open('w') as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                env=dict(os.environ, GHIDRA_HEADLESS_MAXMEM='12G'), start_new_session=True)
        def cancel(sig=None, frame=None):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        signal.signal(signal.SIGTERM, cancel)
        signal.signal(signal.SIGINT, cancel)
        try:
            while proc.poll() is None:
                if shutil.disk_usage(root).free < 8 * GIB:
                    raise RuntimeError('Disk reserve reached; retaining ahead analysis evidence')
                time.sleep(2)
            if proc.returncode:
                raise RuntimeError(f'Analysis exited {proc.returncode}')
            report = json.loads((root / 'depth.json').read_text())
            if not report.get('complete') or report['sha256'] != args.sha256:
                raise RuntimeError('Incomplete ahead analysis report')
            atomic_json(root / 'complete.json', {'complete': True, 'sha256': args.sha256})
        finally:
            cancel()
            proc.wait()


if __name__ == '__main__':
    main()
