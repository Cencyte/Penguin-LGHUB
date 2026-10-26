#!/usr/bin/env python3
"""Run pointer/anchor executables on a newly owned Xvfb via run_private_build.py."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    prefix = Path(os.environ['WINEPREFIX']).resolve()
    if not (prefix / '.overlay-trace-disposable').is_file():
        parser.error('requires an explicitly marked disposable prefix')
    env = dict(os.environ, WINEDEBUG='-all,err+all',
               WINEDLLOVERRIDES='winemenubuilder.exe=d;winebus=d;wineusb=d')
    env.pop('DBUS_SESSION_BUS_ADDRESS', None)
    env.pop('WAYLAND_DISPLAY', None)
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1024x768x24', '-nolisten', 'tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    results = {}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xvfb.stdout, selectors.EVENT_READ)
            if not selector.select(10): raise RuntimeError('owned display failed to start')
            display = xvfb.stdout.readline().decode().strip()
        if not display.isdecimal(): raise RuntimeError('invalid private display')
        env['DISPLAY'] = ':'+display
        for name in ('tray_pointer_test', 'tray_anchor_test'):
            with (args.output / (name+'.log')).open('w') as log:
                r = subprocess.run(['wine', str((args.build / (name+'.exe')).resolve())],
                                   env=env, stdout=log, stderr=subprocess.STDOUT, timeout=60)
            results[name] = r.returncode
        (args.output / 'results.json').write_text(json.dumps(results, indent=2)+'\n')
    finally:
        subprocess.run(['wineserver', '-k'], env=env, timeout=20)
        subprocess.run(['wineserver', '-w'], env=env, timeout=20)
        xvfb.terminate()
        xvfb.wait(timeout=10)
    print(json.dumps(results))
    raise SystemExit(int(any(results.values())))


if __name__ == '__main__': main()
