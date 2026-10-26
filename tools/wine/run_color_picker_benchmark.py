#!/usr/bin/env python3
"""Send real X11 drag events only to an owned Xvfb color-picker fixture.

Use run_private_build.py with a stopped disposable prefix, updater disabled.
Measures event/callback/frame scheduling, not final scanout or hardware lights.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import selectors
import socket
import subprocess
import time

from tested_desktop_launch import prefix_processes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--allow-software-rasterizer', action='store_true')
    p.add_argument('--monitor', type=Path, help='also validate the passive full-application recorder')
    p.add_argument('--trials', type=int, choices=range(1, 4), default=3,
                   help='use one trial for a fresh-process controlled-picker comparison')
    p.add_argument('--drag-seconds', type=int, choices=[3, 12], default=3)
    args = p.parse_args()
    prefix = Path(os.environ['WINEPREFIX']).resolve()
    if not (prefix / '.overlay-trace-disposable').is_file() or prefix_processes(prefix):
        p.error('Use a stopped disposable prefix')
    service = re.search(r'^\[System\\\\ControlSet001\\\\Services\\\\LGHUBUpdaterService\][^\n]*\n(.*?)(?=^\[|\Z)',
                        (prefix / 'system.reg').read_text(), re.M | re.S)
    if not service or '"Start"=dword:00000004' not in service.group(1):
        p.error('Disable the updater service in the disposable fixture prefix')
    args.output.mkdir(parents=True, exist_ok=False)
    xserver = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1280x900x24', '-nolisten', 'tcp'],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    app = display = monitor = None
    env = dict(os.environ, WINEDLLOVERRIDES='winemenubuilder.exe=d', WINEDEBUG='-all')
    for name in ('DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'WAYLAND_DISPLAY'):
        env.pop(name, None)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(xserver.stdout, selectors.EVENT_READ)
            if not selector.select(10):
                raise RuntimeError('Xvfb startup timed out')
        number = xserver.stdout.readline().decode().strip()
        if not number.isdecimal():
            raise RuntimeError('Invalid owned display')
        env['DISPLAY'] = ':' + number
        command = ['wine', str(args.fixture.resolve() / 'lghub.exe'), '--disable-gpu']
        if args.monitor:
            with socket.socket() as debugger:
                debugger.bind(('127.0.0.1', 0))
                port = debugger.getsockname()[1]
            command += ['--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}']
        if not args.allow_software_rasterizer:
            command.append('--disable-software-rasterizer')
        log_path = args.output / 'wine.log'
        with log_path.open('w') as log:
            app = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=args.fixture)
            for _ in range(120):
                if 'BENCHMARK_READY' in log_path.read_text(errors='replace'):
                    break
                if app.poll() is not None:
                    raise RuntimeError('Benchmark exited before readiness')
                time.sleep(.25)
            else:
                raise RuntimeError('Benchmark renderer did not become ready')
            if args.monitor:
                monitor_path = args.output / 'passive-performance.jsonl'
                monitor = subprocess.Popen(['node', str(args.monitor.resolve()), str(port), str(prefix), str(monitor_path)],
                                           stdout=log, stderr=subprocess.STDOUT)
                for _ in range(80):
                    if monitor_path.exists() and '"kind":"attached"' in monitor_path.read_text():
                        break
                    if monitor.poll() is not None:
                        raise RuntimeError('Passive recorder exited before attachment')
                    time.sleep(.1)
                else:
                    raise RuntimeError('Passive recorder did not attach')
            x = ctypes.CDLL('libX11.so.6')
            xt = ctypes.CDLL('libXtst.so.6')
            x.XOpenDisplay.argtypes = [ctypes.c_char_p]; x.XOpenDisplay.restype = ctypes.c_void_p
            x.XFlush.argtypes = [ctypes.c_void_p]; x.XCloseDisplay.argtypes = [ctypes.c_void_p]
            xt.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
            xt.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            display = x.XOpenDisplay(env['DISPLAY'].encode())
            if not display:
                raise RuntimeError('Cannot connect to owned display')
            time.sleep(1)
            for trial in range(args.trials):
                # Separate the positioning move from button-down and avoid
                # successive clicks at the identical coordinate.
                xt.XTestFakeMotionEvent(display, 0, 150 + trial * 12, 150, 0)
                x.XFlush(display)
                time.sleep(.2)
                xt.XTestFakeButtonEvent(display, 1, 1, 0); x.XFlush(display)
                start = time.monotonic()
                for i in range(125 * args.drag_seconds):
                    # Triangle wave stays strictly inside the 500x400 pad.
                    pos = (i % 100) if i % 200 < 100 else 100 - i % 100
                    xt.XTestFakeMotionEvent(display, 0, 150 + pos * 4, 150 + pos * 3, 0)
                    x.XFlush(display)
                    time.sleep(max(0, start + (i + 1) / 125 - time.monotonic()))
                xt.XTestFakeButtonEvent(display, 1, 0, 0); x.XFlush(display)
                deadline = time.monotonic() + 5
                while log_path.read_text(errors='replace').count('BENCHMARK_RESULT ') < trial + 1:
                    if time.monotonic() >= deadline:
                        (args.output / 'failed-trial-windows.txt').write_text(subprocess.run(
                            ['xwininfo', '-root', '-tree'], env=env, capture_output=True, text=True).stdout)
                        raise RuntimeError(f'Trial {trial + 1} did not finish its released drag')
                    time.sleep(.1)
                time.sleep(1)
            reports = []
            for line in log_path.read_text(errors='replace').splitlines():
                if 'BENCHMARK_RESULT ' in line:
                    reports.append(json.loads(line.split('BENCHMARK_RESULT ', 1)[1]))
            result = {'display':env['DISPLAY'], 'command':command, 'trials':reports,
                      'provenance':json.loads((args.fixture / 'provenance.json').read_text())}
            (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            if re.search(r'Uncaught |BENCHMARK_FAILED', log_path.read_text(errors='replace')):
                raise RuntimeError('The fixture reported a JavaScript/renderer failure')
            if len(reports) != args.trials or any(r['pointer_events'] < 100 or r['color_updates'] < 20 for r in reports):
                raise RuntimeError('Insufficient input/render evidence')
            mode = result['provenance'].get('controlled_parent', 'none')
            if mode != 'none' and any(r['backend_commit_count'] != 1 or r['backend_commits_while_held'] != 0 for r in reports):
                raise RuntimeError('Controlled picker did not preserve one deferred commit per drag')
            if mode == 'delayed' and any(r['dot_updates_while_held'] != 0 for r in reports):
                raise RuntimeError('The original controlled picker did not reproduce the frozen dot')
            if mode == 'preview' and any(r['dot_updates_while_held'] < 20 for r in reports):
                raise RuntimeError('The preview fix did not update the dot during dragging')
            if args.monitor:
                records = [json.loads(line) for line in monitor_path.read_text().splitlines()]
                drags = [r for r in records if r['kind'] == 'drag']
                if len(drags) != args.trials or any(r['pointer_events'] < 100 for r in drags):
                    raise RuntimeError('Passive recorder missed the fixture drags')
                if mode == 'delayed' and any(r.get('picker', {}).get('dot_updates') != 0 for r in drags):
                    raise RuntimeError('Dot recorder missed the original frozen state')
                if mode == 'preview' and any(r.get('picker', {}).get('dot_updates', 0) < 20 for r in drags):
                    raise RuntimeError('Dot recorder missed the repaired dot movement')
            print(json.dumps([{k:v for k,v in r.items() if k != 'raw'} for r in reports], indent=2))
    finally:
        if monitor and monitor.poll() is None:
            monitor.terminate(); monitor.wait(timeout=8)
        if display:
            x.XCloseDisplay(display)
        subprocess.run(['wineserver', '-k'], env=env, check=False, timeout=10)
        if app:
            app.wait(timeout=15)
        xserver.terminate(); xserver.wait(timeout=10)


if __name__ == '__main__':
    main()
