#!/usr/bin/env python3
"""Run the input-sink contract test on Xvfb in a copied Wine prefix.

Invoke through run_private_build.py --composition-build-dir BUILD. Pass the
directory containing composition_input_test.exe; --regression may repeat for
existing server regression executables. No hardware or desktop interaction.
"""
import argparse
import os
from pathlib import Path
import selectors
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    parser.add_argument('--regression', type=Path, action='append', default=[])
    parser.add_argument('--delegation', action='store_true', help='also exercise hardware input delegation')
    args = parser.parse_args()
    executable = (args.build_dir / 'composition_input_test.exe').resolve(strict=True)
    regressions = [p.resolve(strict=True) for p in args.regression]
    prefix = Path(os.environ.get('WINEPREFIX', '')).resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    if prefix in (Path.home() / '.wine', repo / 'wine-lab/codex-run/prefix') or not (
            prefix / 'drive_c/Program Files/LGHUB/lghub_agent.exe').is_file():
        parser.error('run in a separate copied G HUB prefix through run_private_build.py')
    args.log_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WINEDEBUG='-all,err+all', WINEDLLOVERRIDES='winemenubuilder.exe=d')
    for name in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'WINE_GHUB_INPUT_SINK',
                 'WINE_GHUB_INPUT_DELEGATION'):
        env.pop(name, None)
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    started = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xvfb.stdout, selectors.EVENT_READ)
            if not selector.select(15):
                raise TimeoutError('Private Xvfb did not start')
            number = xvfb.stdout.readline().decode().strip()
        if not number.isdecimal():
            raise RuntimeError('Invalid private display number')
        env['DISPLAY'] = ':' + number
        jobs = [('disabled', [str(executable), '--disabled'], False),
                ('sink', [str(executable)], True)]
        if args.delegation:
            delegate = (args.build_dir / 'delegated_input_test.exe').resolve(strict=True)
            jobs.extend([('delegation-disabled', [str(delegate), '--disabled'], False),
                         ('delegation', [str(delegate)], True)])
        jobs.extend((p.stem, [str(p)], True) for p in regressions)
        ok = True
        for name, command, enabled in jobs:
            # Unix DLLs read the process environment; each child gets its
            # own opt-in. No success return from an unimplemented fallback.
            test_env = dict(env)
            if enabled:
                test_env['WINE_GHUB_INPUT_SINK'] = '1'
                if args.delegation:
                    test_env['WINE_GHUB_INPUT_DELEGATION'] = '1'
            started = True
            with (args.log_dir / f'{name}.log').open('w') as log:
                result = subprocess.run(['wine', *command], env=test_env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=90)
            print(f'{name}: exit={result.returncode}', flush=True)
            ok = result.returncode == 0 and ok
        return 0 if ok else 1
    finally:
        try:
            if started:
                subprocess.run(['wineserver', '-k'], env=env, timeout=15, check=True)
                subprocess.run(['wineserver', '-w'], env=env, timeout=15, check=True)
        finally:
            xvfb.terminate()
            xvfb.wait(timeout=15)
            xvfb.stdout.close()


if __name__ == '__main__':
    raise SystemExit(main())
