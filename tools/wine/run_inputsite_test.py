#!/usr/bin/env python3
"""Exercise InputSite in private Xvfb and a copied prefix; restore registration.

Invoke through run_private_build.py with --composition-build-dir and
--windows-ui-build-dir. No desktop input or persistent host deployment.
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
    parser.add_argument('--regression', type=Path, action='append', default=[])
    parser.add_argument('--activation-configuration', action='store_true')
    args = parser.parse_args()
    executable = (args.build_dir / ('activation_config_test.exe' if args.activation_configuration
                                    else 'inputsite_test.exe')).resolve(strict=True)
    regressions = [p.resolve(strict=True) for p in args.regression]
    prefix = Path(os.environ.get('WINEPREFIX', '')).resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    if prefix in (Path.home() / '.wine', repo / 'wine-lab/codex-run/prefix') or not (
            prefix / 'drive_c/Program Files/LGHUB/lghub_agent.exe').is_file():
        parser.error('run through run_private_build.py in a stopped copied G HUB prefix')
    args.log_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WINEDEBUG='-all,err+all', WINEDLLOVERRIDES='winemenubuilder.exe=d',
               WINE_GHUB_INPUT_SINK='1')
    for name in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'WINE_GHUB_INPUT_SITE'):
        env.pop(name, None)
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    keys = [r'HKLM\Software\Microsoft\WindowsRuntime\ActivatableClassId\Windows.UI.Internal.Input.InputSite']
    if args.activation_configuration:
        keys.append(r'HKLM\Software\Microsoft\WindowsRuntime\ActivatableClassId\Windows.UI.Internal.Input.ActivationConfigurationInputObject')
    started = False
    registry = []
    backup = tempfile.TemporaryDirectory(prefix='ghub-inputsite-')

    def reg(*args, check=True):
        return subprocess.run(['wine', 'reg', *args], env=env, check=check,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xvfb.stdout, selectors.EVENT_READ)
            if not selector.select(15):
                raise TimeoutError('Private Xvfb did not start')
            number = xvfb.stdout.readline().decode().strip()
        if not number.isdecimal():
            raise RuntimeError('Invalid private display number')
        env['DISPLAY'] = ':' + number
        started = True
        for i, key in enumerate(keys):
            registry_file = 'Z:' + str(Path(backup.name) / f'{i}.reg').replace('/', '\\')
            existed = reg('query', key, check=False).returncode == 0
            if existed:
                reg('export', key, registry_file, '/y')
            registry.append((key, registry_file if existed else None))
            reg('add', key, '/v', 'DllPath', '/d', r'C:\windows\system32\windows.ui.dll', '/f')
            reg('add', key, '/v', 'ThreadingModel', '/t', 'REG_DWORD', '/d', '3', '/f')
        jobs = [('disabled', [str(executable), '--disabled'], False),
                ('inputsite', [str(executable)], True),
                ('framework', [str(executable), '--framework'], True)]
        if args.activation_configuration:
            jobs = [('disabled', [str(executable), '--disabled'], False),
                    ('activation_configuration', [str(executable)], True)]
        jobs.extend((p.stem, [str(p)], True) for p in regressions)
        ok = True
        for name, command, enabled in jobs:
            test_env = dict(env)
            if enabled:
                test_env['WINE_GHUB_INPUT_SITE'] = '1'
            with (args.log_dir / f'{name}.log').open('w') as log:
                result = subprocess.run(['wine', *command], env=test_env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=90)
            print(f'{name}: exit={result.returncode}', flush=True)
            ok = result.returncode == 0 and ok
        return 0 if ok else 1
    finally:
        try:
            for key, registry_file in reversed(registry):
                reg('delete', key, '/f', check=False)
                if registry_file:
                    reg('import', registry_file)
        finally:
            try:
                if started:
                    subprocess.run(['wineserver', '-k'], env=env, timeout=15, check=True)
                    subprocess.run(['wineserver', '-w'], env=env, timeout=15, check=True)
            finally:
                backup.cleanup()
                xvfb.terminate()
                xvfb.wait(timeout=15)
                xvfb.stdout.close()


if __name__ == '__main__':
    raise SystemExit(main())
