#!/usr/bin/env python3
"""Run corewindow_test.exe on Xvfb inside run_private_build.py's copied prefix.

Use --expect-missing without --windows-ui-build-dir to confirm the baseline;
use --register with the private Windows.UI build for the patched regression.
Registration only changes this copied Wine prefix, never the desktop prefix.
"""
import argparse
import os
from pathlib import Path
import selectors
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--log', required=True, type=Path)
    parser.add_argument('--uisettings-executable', type=Path,
                        help='also test UISettings animation preferences in this isolated session')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--expect-missing', action='store_true')
    mode.add_argument('--register', action='store_true')
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    uisettings = args.uisettings_executable.resolve(strict=True) if args.uisettings_executable else None
    if not os.environ.get('WINEPREFIX'):
        parser.error('run inside run_private_build.py with a stopped copied G HUB prefix')
    prefix = Path(os.environ['WINEPREFIX']).resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    if prefix in (Path.home() / '.wine', repo / 'wine-lab/codex-run/prefix') or not (prefix / 'drive_c/Program Files/LGHUB/lghub_agent.exe').is_file():
        parser.error('a separate copied G HUB prefix is required')
    env = os.environ.copy()
    env.update(WINEDEBUG='-all,err+all,trace+ui', WINEDLLOVERRIDES='winemenubuilder.exe=d')
    for name in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    started = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xvfb.stdout, selectors.EVENT_READ)
            if not selector.select(15):
                raise TimeoutError('Private Xvfb did not start')
            display = xvfb.stdout.readline().decode().strip()
        if not display.isdecimal():
            raise RuntimeError('Private Xvfb returned no display')
        env['DISPLAY'] = ':' + display
        command = ['wine', str(executable)]
        if args.expect_missing:
            command.append('--expect-missing')
        if args.register:
            command.append('--register')
        started = True
        with args.log.open('w') as output:
            result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=60)
            if not result.returncode and uisettings:
                result = subprocess.run(['wine', str(uisettings)], env=env,
                                        stdout=output, stderr=subprocess.STDOUT, timeout=60)
        print(f'CoreWindow regression exit={result.returncode}; log={args.log}', flush=True)
        return result.returncode
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
