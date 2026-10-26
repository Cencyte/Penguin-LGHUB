#!/usr/bin/env python3
"""Run the shared-texture/context regressions on an owned private Xvfb display.

Invoke inside run_private_build.py with a stopped, disposable copied prefix and
the matched D3D11/DXGI/WineD3D trio. Never selects or injects into the live display.
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
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    parser.add_argument('--window-loss-only', action='store_true')
    parser.add_argument('--adapters-only', action='store_true', help='run DXGI adapter notifications and controlled topology tests')
    parser.add_argument('--force-backup-dc', action='store_true', help='enable explicit test-only recovery injection')
    args = parser.parse_args()
    if args.force_backup_dc and not args.window_loss_only:
        parser.error('--force-backup-dc requires --window-loss-only')
    if args.adapters_only and (args.window_loss_only or args.force_backup_dc):
        parser.error('--adapters-only cannot be combined with window-loss options')
    prefix = Path(os.environ['WINEPREFIX']).resolve(strict=True)
    if not (prefix / '.overlay-trace-disposable').is_file():
        parser.error('requires an explicitly marked disposable prefix')
    build = args.build_dir.resolve(strict=True)
    executable = build / ('dxgi_adapter_notifications_test.exe' if args.adapters_only else 'd3d11_shared_texture_test.exe')
    if not executable.is_file():
        parser.error(f'missing test executable: {executable}')
    args.log_dir.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, WINEDEBUG='-all,err+all,warn+d3d', GHUB_D3D11_SHARED_TEXTURES='1',
               GHUB_D3D11_CONTEXT_TRACE='1', GHUB_DXGI_ENQUEUE_EVENT='1',
               WINEDLLOVERRIDES='winemenubuilder.exe=d')
    if args.force_backup_dc: env['GHUB_D3D11_TEST_BACKUP_DC'] = '1'
    else: env.pop('GHUB_D3D11_TEST_BACKUP_DC', None)
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    components = [Path('/usr/lib/wine/x86_64-windows') / name for name in ('d3d11.dll', 'dxgi.dll', 'wined3d.dll')]
    (args.log_dir / 'inputs.json').write_text(json.dumps({
        'prefix': str(prefix), 'forced_backup_dc': args.force_backup_dc, 'test_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
        'components': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in components},
    }, indent=2) + '\n')
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    results = {}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xvfb.stdout, selectors.EVENT_READ)
            if not selector.select(15):
                raise TimeoutError('Private Xvfb startup')
            number = xvfb.stdout.readline().decode().strip()
        if not number.isdecimal():
            raise RuntimeError('Invalid private display')
        env['DISPLAY'] = ':' + number
        jobs = [('adapter-notifications', [str(executable)])] if args.adapters_only else [('window-loss', [str(executable), '--window-loss'])]
        if not args.window_loss_only and not args.adapters_only:
            jobs += [('shared-textures', [str(executable)]),
                     ('enqueue-event', [str(build / 'dxgi_enqueue_event_test.exe')])]
        for name, command in jobs:
            with (args.log_dir / f'{name}.log').open('w') as log:
                result = subprocess.run(['wine', *command], env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=120)
            if args.force_backup_dc and 'GHUB TEST: forcing application-DC to backup-DC recovery.' not in (args.log_dir / f'{name}.log').read_text(errors='replace'):
                raise RuntimeError('Recovery injection did not execute; test is inconclusive')
            results[name] = result.returncode
            print(f'{name}: exit={result.returncode}', flush=True)
        (args.log_dir / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        return int(any(results.values()))
    finally:
        try:
            if 'DISPLAY' in env:
                subprocess.run(['wineserver', '-k'], env=env, timeout=15, check=True)
                subprocess.run(['wineserver', '-w'], env=env, timeout=15, check=True)
        finally:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=15)
            except subprocess.TimeoutExpired:
                xvfb.kill()
                xvfb.wait(timeout=5)
            xvfb.stdout.close()


if __name__ == '__main__':
    raise SystemExit(main())
