#!/usr/bin/env python3
"""Headless timer regressions, inside run_private_build.py's mount namespace.

Uses only an explicitly marked disposable prefix. Registers the timer there,
records the mounted module hash, and shuts down that prefix's Wine server.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prefix = Path(os.environ['WINEPREFIX']).resolve(strict=True)
    if not (prefix / '.overlay-trace-disposable').is_file():
        parser.error('requires a marked disposable prefix')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    for name in ('DBUS_SESSION_BUS_ADDRESS', 'WAYLAND_DISPLAY'):
        env.pop(name, None)
    env['WINEDEBUG'] = '-all,err+all,trace+threadpool,trace+loaddll'
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xvfb.stdout, selectors.EVENT_READ)
            if not selector.select(10):
                raise RuntimeError('Xvfb did not start')
            display = xvfb.stdout.readline().decode().strip()
            if not display.isdecimal():
                raise RuntimeError('Invalid Xvfb display')
        env['DISPLAY'] = ':' + display
        key = r'HKLM\Software\Microsoft\WindowsRuntime\ActivatableClassId\Windows.System.Threading.ThreadPoolTimer'
        with (output / 'setup.log').open('w') as log:
            for values in (['/v', 'DllPath', '/d', r'C:\windows\system32\threadpoolwinrt.dll'],
                           ['/v', 'ThreadingModel', '/t', 'REG_DWORD', '/d', '3']):
                subprocess.run(['wine', 'reg', 'add', key, *values, '/f'], env=env,
                               stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
        with (output / 'tests.log').open('w') as log:
            result = subprocess.run(['wine', str(args.executable.resolve(strict=True))], env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=90)
        module = Path('/usr/lib/wine/x86_64-windows/threadpoolwinrt.dll')
        summary = {'exit_code': result.returncode, 'prefix': str(prefix),
                   'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
                   'test_sha256': hashlib.sha256(args.executable.read_bytes()).hexdigest()}
        (output / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary), flush=True)
        raise SystemExit(result.returncode)
    finally:
        try:
            subprocess.run(['wineserver', '-k'], env=env, timeout=20, check=False)
            subprocess.run(['wineserver', '-w'], env=env, timeout=20, check=False)
        finally:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=5)
            except subprocess.TimeoutExpired:
                xvfb.kill()
                xvfb.wait()


if __name__ == '__main__':
    main()
