#!/usr/bin/env python3
"""Freeze owned DragonShard services before a protected disk fills or its mount disappears."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def services():
    rows = subprocess.check_output(['systemctl', '--user', 'list-units', '--type=service', '--state=running',
                                    '--no-legend', '--plain', '--no-pager'], text=True)
    return [line.split()[0] for line in rows.splitlines() if line.startswith('dragonshard2-')
            and not any(excluded in line.split()[0] for excluded in ('relocate', 'disk-guard'))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('status', type=Path)
    parser.add_argument('--protect', action='append', required=True, help='PATH:minimum-free-GiB')
    parser.add_argument('--require-mount', action='append', default=[])
    args = parser.parse_args()
    paths = [(Path(p), float(g) * 1024**3) for p, g in (item.rsplit(':', 1) for item in args.protect)]
    mounts = {p: os.stat(p).st_dev for p in args.require_mount}
    paused = set()
    while True:
        checks, reasons = [], []
        for name, device in mounts.items():
            if not os.path.ismount(name) or os.stat(name).st_dev != device:
                reasons.append('Required storage mount disappeared: ' + name)
        for path, minimum in paths:
            free = shutil.disk_usage(path).free
            checks.append(dict(path=str(path), free_bytes=free, minimum_free_bytes=minimum))
            if free < minimum:
                reasons.append(str(path) + ' reached its disk protection threshold')
        if reasons:
            for unit in services():
                state = subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'FreezerState', '--value'], text=True).strip()
                if state != 'frozen':
                    subprocess.run(['systemctl', '--user', 'freeze', unit], check=True); paused.add(unit)
        elif paused:
            # Resume only freezes this guard owns. Preserve other intentional pauses.
            for unit in list(paused):
                subprocess.run(['systemctl', '--user', 'thaw', unit], check=False); paused.remove(unit)
        state = dict(at=time.time(), state='disk_protection_active' if reasons else 'watching',
                     checks=checks, reasons=reasons, guard_paused_units=sorted(paused))
        temporary = args.status.with_suffix('.json.partial')
        temporary.write_text(json.dumps(state, indent=2) + '\n'); temporary.replace(args.status)
        time.sleep(2)


if __name__ == '__main__':
    main()
