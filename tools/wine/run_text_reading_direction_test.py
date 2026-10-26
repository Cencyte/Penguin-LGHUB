#!/usr/bin/env python3
"""Run the DirectWrite ABI test on private Xvfb in a stopped, copied prefix.

Use run_private_build.py --dwrite-build-dir BUILD for the candidate DLL.
--native-reference optionally compares against a local Microsoft DWrite.dll;
it is copied under a distinct name in a temporary directory, never installed.
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
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--native-reference', type=Path)
    parser.add_argument('--render-executable', type=Path, help='also verify native D2D text pixels')
    parser.add_argument('--native-d2d', type=Path, help='local native d2d1.dll for the render test')
    args = parser.parse_args()
    if bool(args.render_executable) != bool(args.native_d2d):
        parser.error('--render-executable and --native-d2d must be supplied together')
    repo = Path(__file__).resolve().parents[2]
    if not os.environ.get('WINEPREFIX'):
        parser.error('set WINEPREFIX to a copied laboratory prefix')
    prefix = Path(os.environ['WINEPREFIX']).resolve(strict=True)
    if prefix in (Path.home() / '.wine', repo / 'wine-lab/codex-run/prefix'):
        parser.error('use a copied laboratory prefix')
    if not (prefix / 'drive_c/Program Files/LGHUB/lghub_agent.exe').is_file():
        parser.error('a copied G HUB prefix is required')
    executable = args.executable.resolve(strict=True)
    native = args.native_reference.resolve(strict=True) if args.native_reference else None
    render_source = args.render_executable.resolve(strict=True) if args.render_executable else None
    d2d_source = args.native_d2d.resolve(strict=True) if args.native_d2d else None
    args.log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WINEDEBUG='-all,err+all', WINEDLLOVERRIDES='winemenubuilder.exe=d;dwrite=b')
    for name in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xvfb.stdout, selectors.EVENT_READ)
            if not selector.select(15):
                raise TimeoutError('Private Xvfb did not start')
            number = xvfb.stdout.readline().decode().strip()
        if not number.isdecimal():
            raise RuntimeError('Invalid Xvfb display number')
        env['DISPLAY'] = ':' + number
        with tempfile.TemporaryDirectory(prefix='ghub-dwrite-') as directory:
            command = ['wine', str(executable)]
            if native:
                reference = Path(directory) / 'native_dwrite.dll'
                shutil.copy2(native, reference)
                command.append('Z:' + str(reference).replace('/', '\\'))
            if args.render_executable:
                render = Path(directory) / 'dwrite_d2d_render_test.exe'
                shutil.copy2(render_source, render)
                shutil.copy2(d2d_source, Path(directory) / 'd2d1.dll')
            with args.log.open('w') as log:
                result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90)
                if not result.returncode and args.render_executable:
                    render_env = dict(env, WINEDLLOVERRIDES=env['WINEDLLOVERRIDES'] + ';d2d1=n')
                    render_env['GHUB_D3D11_PARTNER_PROBE'] = '1'
                    result = subprocess.run(['wine', str(render)], env=render_env, stdout=log,
                                            stderr=subprocess.STDOUT, timeout=90)
            print(f'DirectWrite tests: exit={result.returncode}; log={args.log}', flush=True)
            return result.returncode
    finally:
        try:
            subprocess.run(['wineserver', '-k'], env=env, check=True, timeout=15)
            subprocess.run(['wineserver', '-w'], env=env, check=True, timeout=30)
        finally:
            xvfb.terminate()
            xvfb.wait(timeout=15)


if __name__ == '__main__':
    raise SystemExit(main())
