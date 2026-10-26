#!/usr/bin/env python3
"""Test the opt-in dispatcher adapter in a copied prefix on private Xvfb.

Invoke through run_private_build.py with the matched composition build. Files
and the activation registration are restored, including on a failed test.
"""
import argparse
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    args = parser.parse_args()
    build = args.build_dir.resolve(strict=True)
    prefix = Path(os.environ.get('WINEPREFIX', '')).resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    if prefix in (Path.home() / '.wine', repo / 'wine-lab/codex-run/prefix'):
        parser.error('use a copied laboratory prefix')
    tray = prefix / 'drive_c/Program Files/LGHUB/system_tray'
    if not (tray / 'CoreMessagingXP.dll').is_file():
        parser.error('a copied G HUB prefix is required')
    for name in ('coremessaging.dll', 'ghub_dispatcher_queue.dll',
                 'dispatcher_queue_test.exe', 'coremessaging_adapter_test.exe'):
        (build / name).resolve(strict=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WINEDEBUG='-all,err+all', GHUB_DISPATCHER_TRACE='1',
               WINEDLLOVERRIDES='winemenubuilder.exe=d;coremessaging=n;d2d1=b')
    for name in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    key = r'HKLM\Software\Microsoft\WindowsRuntime\ActivatableClassId\Windows.System.DispatcherQueue'
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix='ghub-dispatcher-') as temp:
        backup = Path(temp)
        staged = []
        registered = False
        existed = False

        def reg(*command, check=True):
            return subprocess.run(['wine', 'reg', *command], env=env, check=check,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)

        try:
            with selectors.DefaultSelector() as selector:
                selector.register(xvfb.stdout, selectors.EVENT_READ)
                if not selector.select(15):
                    raise TimeoutError('Private Xvfb did not start')
                number = xvfb.stdout.readline().decode().strip()
            if not number.isdecimal():
                raise RuntimeError('Invalid Xvfb display number')
            env['DISPLAY'] = ':' + number
            for source, name in [('coremessaging.dll', 'CoreMessaging.dll'),
                                 ('ghub_dispatcher_queue.dll', 'ghub_dispatcher_queue.dll')]:
                destination = tray / name
                old = backup / name if destination.exists() else None
                if old:
                    shutil.copy2(destination, old)
                staged.append((destination, old))
                shutil.copy2(build / source, destination)
            existed = reg('query', key, check=False).returncode == 0
            saved_reg = 'Z:' + str(backup / 'queue.reg').replace('/', '\\')
            if existed:
                reg('export', key, saved_reg, '/y')
            registered = True
            reg('add', key, '/v', 'DllPath', '/d',
                r'C:\Program Files\LGHUB\system_tray\ghub_dispatcher_queue.dll', '/f')
            reg('add', key, '/v', 'ThreadingModel', '/t', 'REG_DWORD', '/d', '3', '/f')
            success = True
            for executable in ('coremessaging_adapter_test.exe', 'dispatcher_queue_test.exe'):
                with (args.log_dir / (Path(executable).stem + '.log')).open('w') as log:
                    result = subprocess.run(['wine', str(build / executable), str(tray / 'CoreMessaging.dll')],
                                            env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90)
                print(f'{executable}: exit={result.returncode}', flush=True)
                success = result.returncode == 0 and success
            return 0 if success else 1
        finally:
            try:
                if registered:
                    reg('delete', key, '/f')
                    if existed:
                        reg('import', saved_reg)
            finally:
                try:
                    subprocess.run(['wineserver', '-k'], env=env, timeout=15, check=True)
                    subprocess.run(['wineserver', '-w'], env=env, timeout=15, check=True)
                finally:
                    for destination, old in reversed(staged):
                        if old:
                            shutil.copy2(old, destination)
                        else:
                            destination.unlink(missing_ok=True)
                    xvfb.terminate()
                    xvfb.wait(timeout=15)
                    xvfb.stdout.close()


if __name__ == '__main__':
    raise SystemExit(main())
