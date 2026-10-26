#!/usr/bin/env python3
"""Probe native ninput in a stopped disposable prefix on an owned Xvfb.

Run through run_private_build.py with the experimental native-input ordinals,
inside a private network namespace. The device fixture exists only in the probe;
passing these checks does not establish real application input delivery.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess

from tested_desktop_launch import prefix_processes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--native-dll', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prefix = Path(os.environ['WINEPREFIX']).resolve()
    host_net = subprocess.check_output(['sudo','-n','readlink','/proc/1/ns/net'],text=True).strip()
    if os.readlink('/proc/self/ns/net') == host_net:
        parser.error('requires private network namespace')
    if not (prefix/'.overlay-trace-disposable').is_file() or prefix_processes(prefix):
        parser.error('requires stopped disposable prefix')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.probe, args.output/'ninput_native_probe.exe')
    shutil.copy2(args.native_dll, args.output/'ninput_native.dll')
    env = dict(os.environ, WINEDEBUG='-all,err+all', GHUB_TRAY_PRIVATE_LAB='1',
               WINEDLLOVERRIDES='winemenubuilder.exe=d;winebus.sys=d;wineusb.sys=d;winedbg.exe=d;ninput_native=n')
    for name in ('DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    xvfb = subprocess.Popen(['Xvfb','-displayfd','1','-screen','0','1024x768x24','-nolisten','tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    results = {}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xvfb.stdout, selectors.EVENT_READ)
            if not selector.select(10):
                raise RuntimeError('Private Xvfb did not start')
        display = xvfb.stdout.readline().decode().strip()
        if not display.isdecimal():
            raise RuntimeError('Invalid private display')
        env['DISPLAY'] = ':'+display
        cases = [('tap', ['--device-fixture'], 0, 'tap callbacks=1'),
                 ('null-device', ['--null-device'], 1, 'hr=80400003'),
                 ('missing-geometry', [], 1, 'hr=80400006')]
        for name, flags, expected, evidence in cases:
            log = args.output/(name+'.log')
            with log.open('w') as output:
                run = subprocess.run(['wine', str(args.output/'ninput_native_probe.exe')]+flags,
                                     env=env, stdout=output, stderr=subprocess.STDOUT, timeout=35)
            results[name] = dict(exit=run.returncode, expected=expected,
                                passed=run.returncode == expected and evidence in log.read_text())
    finally:
        subprocess.run(['wineserver','-k'], env=env, timeout=15)
        subprocess.run(['wineserver','-w'], env=env, timeout=15)
        xvfb.terminate()
        xvfb.wait(timeout=10)
        (args.output/'result.json').write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps(results))
    return int(len(results) != 3 or not all(r['passed'] for r in results.values()))


if __name__ == '__main__':
    raise SystemExit(main())
