#!/usr/bin/env python3
"""Run memory-stream ABI tests in a copied prefix on private Xvfb.

Invoke through run_private_build.py --windows-storage-build-dir BUILD.
The temporary activation registration is restored even after failed tests.
"""
import argparse
import os
from pathlib import Path
import selectors
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    parser.add_argument('--data-writer', action='store_true',
                        help='register the private DataWriter and also run its ABI tests')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    prefix_value = os.environ.get('WINEPREFIX')
    if not prefix_value:
        parser.error('set WINEPREFIX to a copied laboratory prefix')
    prefix = Path(prefix_value).resolve(strict=True)
    if prefix in (Path.home() / '.wine', repo / 'wine-lab/codex-run/prefix'):
        parser.error('use a copied laboratory prefix')
    if not (prefix / 'drive_c/Program Files/LGHUB/lghub_agent.exe').is_file():
        parser.error('a copied G HUB prefix is required')
    executable = (args.build_dir / 'dlls/windows.storage/tests/x86_64-windows/windows.storage_test.exe').resolve(strict=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WINEDEBUG='-all,err+all,trace+storage',
               WINEDLLOVERRIDES='winemenubuilder.exe=d;windows.storage=b', WINETEST_DEBUG='1')
    for name in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    classes = ['InMemoryRandomAccessStream']
    if args.data_writer:
        classes.append('DataWriter')
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix='ghub-stream-') as temp:
        registrations = []

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
            for class_name in classes:
                key = ('HKLM\\Software\\Microsoft\\WindowsRuntime\\ActivatableClassId\\'
                       'Windows.Storage.Streams.' + class_name)
                existed = reg('query', key, check=False).returncode == 0
                saved_reg = 'Z:' + str(Path(temp) / (class_name + '.reg')).replace('/', '\\')
                if existed:
                    reg('export', key, saved_reg, '/y')
                registrations.append((key, saved_reg if existed else None))
                reg('add', key, '/v', 'DllPath', '/d', r'C:\windows\system32\windows.storage.dll', '/f')
                reg('add', key, '/v', 'ThreadingModel', '/t', 'REG_DWORD', '/d', '3', '/f')
            success = True
            for test in (['storage', 'writer'] if args.data_writer else ['storage']):
                log_path = args.log_dir / ('memory-stream.log' if test == 'storage' else 'data-writer.log')
                with log_path.open('w') as log:
                    result = subprocess.run(['wine', str(executable), test], env=env,
                                            stdout=log, stderr=subprocess.STDOUT, timeout=90)
                print(f'{test} tests: exit={result.returncode}; log={log_path}', flush=True)
                success = result.returncode == 0 and success
            return 0 if success else 1
        finally:
            try:
                for key, saved_reg in reversed(registrations):
                    reg('delete', key, '/f')
                    if saved_reg:
                        reg('import', saved_reg)
            finally:
                try:
                    subprocess.run(['wineserver', '-k'], env=env, timeout=15, check=True)
                    subprocess.run(['wineserver', '-w'], env=env, timeout=15, check=True)
                finally:
                    xvfb.terminate()
                    xvfb.wait(timeout=15)
                    xvfb.stdout.close()


if __name__ == '__main__':
    raise SystemExit(main())
