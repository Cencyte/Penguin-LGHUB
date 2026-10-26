#!/usr/bin/env python3
"""Check native hover input and X11 border pixels using only an owned Xvfb.

Run via run_private_build.py in a stopped disposable prefix. Uses inert artwork
and the installed CSS/Electron; never connects to the agent or real peripherals.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time

from tested_desktop_launch import prefix_processes


def records(path, marker):
    return [json.loads(line.split(marker, 1)[1]) for line in path.read_text(errors='replace').splitlines()
            if marker in line]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('fixture', 'config', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--layout', choices=['grid', 'list'], default='grid')
    p.add_argument('--no-xrender', action='store_true')
    p.add_argument('--expect-failure', action='store_true', help='Require correct input but stale displayed borders')
    args = p.parse_args()
    prefix = Path(os.environ['WINEPREFIX']).resolve()
    if not (prefix / '.overlay-trace-disposable').is_file() or prefix_processes(prefix):
        p.error('Use a stopped disposable prefix')
    service = re.search(r'^\[System\\\\ControlSet001\\\\Services\\\\LGHUBUpdaterService\][^\n]*\n(.*?)(?=^\[|\Z)',
                        (prefix / 'system.reg').read_text(), re.M | re.S)
    if not service or '"Start"=dword:00000004' not in service.group(1):
        p.error('Disable the updater in the disposable prefix')
    args.output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.update(json.loads(args.config.read_text())['environment'])
    env.update(WINEDLLOVERRIDES='winemenubuilder.exe=d;winebus.sys=d;wineusb.sys=d', WINEDEBUG='-all')
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    result = {'layout':args.layout, 'xrender_disabled':args.no_xrender, 'samples':[],
              'provenance':json.loads((args.fixture / 'provenance.json').read_text())}
    server = process = display = None
    try:
        xcommand = ['Xvfb', '-displayfd', '1', '-screen', '0', '1280x900x24', '-nolisten', 'tcp']
        if args.no_xrender:
            xcommand += ['-extension', 'RENDER']
        server = subprocess.Popen(xcommand, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        with selectors.DefaultSelector() as selector:
            selector.register(server.stdout, selectors.EVENT_READ)
            if not selector.select(10):
                raise RuntimeError('Owned Xvfb did not start')
        number = server.stdout.readline().decode().strip()
        if not number.isdecimal():
            raise RuntimeError('Invalid owned display')
        env['DISPLAY'] = ':' + number
        result['display'] = env['DISPLAY']
        extensions = subprocess.check_output(['xdpyinfo', '-queryExtensions'], env=env, text=True, timeout=10)
        (args.output / 'x-extensions.txt').write_text(extensions)
        result['render_extension_present'] = bool(re.search(r'^\s+RENDER\s', extensions, re.M))
        if result['render_extension_present'] == args.no_xrender:
            raise RuntimeError('Owned display does not have the requested RENDER extension state')
        x, xt = ctypes.CDLL('libX11.so.6'), ctypes.CDLL('libXtst.so.6')
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]; x.XOpenDisplay.restype = ctypes.c_void_p
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]; x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                               ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
        x.XGetImage.restype = ctypes.c_void_p
        x.XGetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]; x.XGetPixel.restype = ctypes.c_ulong
        x.XDestroyImage.argtypes = [ctypes.c_void_p]
        x.XFlush.argtypes = [ctypes.c_void_p]; x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xt.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        display = x.XOpenDisplay(env['DISPLAY'].encode())
        if not display:
            raise RuntimeError('Cannot open owned display')
        root = x.XDefaultRootWindow(display)

        def pixel(px, py):
            px, py = int(px), int(py)
            if not (0 <= px < 1280 and 0 <= py < 900):
                raise RuntimeError('Sample escaped the owned display')
            image = x.XGetImage(display, root, px, py, 1, 1, ctypes.c_ulong(-1).value, 2)
            if not image:
                raise RuntimeError('XGetImage failed')
            try:
                value = x.XGetPixel(image, 0, 0)
                return [(value >> shift) & 255 for shift in (16, 8, 0)]
            finally:
                x.XDestroyImage(image)

        log_path = args.output / 'wine.log'
        command = ['wine', str(args.fixture.resolve() / 'lghub.exe'), '--disable-gpu', '--disable-software-rasterizer']
        if args.layout == 'list':
            command.append('--hover-list')
        result['command'] = command
        with log_path.open('w') as log:
            process = subprocess.Popen(command, env=env, cwd=args.fixture, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 45
            while not (ready := records(log_path, 'HOVER_READY ')):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Fixture did not become ready')
                time.sleep(.1)
            rects = [c['rect'] for c in ready[-1]]
            if len(rects) != 3:
                raise RuntimeError('Expected three visible cards')
            for trial in range(3):
                for index, rect in enumerate(rects):
                    points = [(5, 5), (.35 * rect['width'], 20), (.35 * rect['width'], .3 * rect['height']),
                              (.35 * rect['width'], .5 * rect['height']), (rect['width'] - 10, .5 * rect['height']),
                              (.35 * rect['width'], rect['height'] - 40), (-10, .3 * rect['height'])]
                    for dx, dy in points:
                        px, py = int(rect['x'] + dx), int(rect['y'] + dy)
                        xt.XTestFakeMotionEvent(display, 0, px, py, 0); x.XFlush(display)
                        time.sleep(.15)  # automated fixture settling, never a user deadline
                        samples = records(log_path, 'HOVER_SAMPLE ')
                        sample = samples[-1] if samples else None
                        if not sample or (sample['x'], sample['y']) != (px, py):
                            raise RuntimeError('Native pointer did not reach the renderer')
                        if [c['hover'] for c in sample['cards']] != [dx >= 0 and i == index for i in range(3)]:
                            raise RuntimeError('DOM hover did not match native pointer position')
                        checks = []
                        for card, geometry in zip(sample['cards'], rects):
                            expected = [255, 255, 255] if card['hover'] else [33, 34, 37]
                            color = 'rgb(255, 255, 255)' if card['hover'] else 'rgba(0, 0, 0, 0)'
                            if card['color'] != color or card['background'] != 'rgb(33, 34, 37)':
                                raise RuntimeError('Unexpected vendor theme; update the pixel oracle')
                            gx, gy, width, height = (geometry[k] for k in ('x', 'y', 'width', 'height'))
                            edges = [(gx + width / 2, gy), (gx, gy + height / 2),
                                     (gx + width - 1, gy + height / 2), (gx + width / 2, gy + height - 1)]
                            for point in edges:
                                actual = pixel(*point)
                                checks.append({'point':point, 'actual':actual, 'expected':expected, 'pass':actual == expected})
                        result['samples'].append({'trial':trial, 'pointer':[px, py], 'dom':sample, 'pixels':checks})
            if re.search(r'Uncaught |HOVER_FAILED', log_path.read_text(errors='replace')):
                raise RuntimeError('Fixture renderer failure')
        result['pixel_pass'] = sum(p['pass'] for s in result['samples'] for p in s['pixels'])
        result['pixel_total'] = sum(len(s['pixels']) for s in result['samples'])
        result['input_samples'] = len(result['samples'])
        result['pass'] = result['input_samples'] == 63 and result['pixel_pass'] == result['pixel_total']
        result['negative_control_pass'] = args.expect_failure and result['input_samples'] == 63 and result['pixel_pass'] < result['pixel_total']
    except Exception as error:
        result.update({'pass':False, 'error':str(error)})
    finally:
        if display:
            x.XCloseDisplay(display)
        if server:
            subprocess.run(['wineserver', '-k'], env=env, timeout=10, check=False)
            subprocess.run(['wineserver', '-w'], env=env, timeout=15, check=False)
        if process:
            process.wait(timeout=10)
        if server:
            server.terminate(); server.wait(timeout=10)
        (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('samples', 'command', 'provenance')}, indent=2))
    success = result.get('negative_control_pass') if args.expect_failure else result.get('pass')
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
