#!/usr/bin/env python3
"""Exercise real Electron caption drags and pixels on an owned Xvfb/KWin display.

Run through run_private_build.py in a stopped, disposable prefix with the
LGHUBUpdaterService disabled. --fixture comes from prepare_electron_window_fixture.py.
The software-only flags isolate X11/window behavior from unavailable headless
Vulkan; this is not a hardware/WebGL acceptance test.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time

from run_dcomp_tests import Capture
from tested_desktop_launch import prefix_processes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--command-probe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--child-composition', choices=['0', '1'], default='1')
    parser.add_argument('--allow-software-rasterizer', action='store_true')
    args = parser.parse_args()
    prefix = args.prefix.resolve(strict=True)
    if Path(os.environ.get('WINEPREFIX', '')).resolve() != prefix:
        parser.error('Run through the private Wine runner')
    if not (prefix / '.overlay-trace-disposable').is_file() or prefix_processes(prefix):
        parser.error('Use a stopped disposable prefix')
    registry = (prefix / 'system.reg').read_text()
    service = re.search(r'^\[System\\\\ControlSet001\\\\Services\\\\LGHUBUpdaterService\][^\n]*\n(.*?)(?=^\[|\Z)',
                        registry, re.M | re.S)
    if not service or '"Start"=dword:00000004' not in service.group(1):
        parser.error('Disable LGHUBUpdaterService in this disposable prefix before the fixture test')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.update(json.loads(args.config.read_text())['environment'])
    env.update(WINEDLLOVERRIDES='winemenubuilder.exe=d', WINE_GHUB_CHILD_COMPOSITION=args.child_composition,
               WINEDEBUG='-all,err+all' + (',trace+event,trace+win,trace+message,trace+cursor' if args.trace else ''))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    results = {'drags':[], 'keyboard':{}, 'pixels':{}, 'software_rasterizer':args.allow_software_rasterizer}
    xserver = wm = app = capture = None
    logs = []

    def xdo(*argv):
        return subprocess.check_output(['xdotool', *map(str, argv)], env=env, text=True, timeout=10).strip()

    def geometry(window):
        return dict((k, int(v)) for k, v in (s.split('=') for s in xdo('getwindowgeometry', '--shell', window).splitlines()))

    def sample(window, label):
        g = geometry(window)
        points = {'green':(150,35,[0,204,68]), 'red':(150,220,[238,34,68]),
                  'blue':(550,450,[34,85,238]), 'background':(350,350,[32,32,32])}
        data = {}
        for name, (a, b, expected) in points.items():
            x, y = g['X'] + a, g['Y'] + b
            if not (0 <= x < 1600 and 0 <= y < 1100):
                raise RuntimeError('Fixture escaped the private display')
            image = capture.x.XGetImage(capture.display, capture.overlay, x, y, 1, 1, ctypes.c_ulong(-1).value, 2)
            if not image:
                raise RuntimeError('XGetImage failed')
            try:
                value = capture.x.XGetPixel(image, 0, 0)
            finally:
                capture.x.XDestroyImage(image)
            actual = [(value >> shift) & 255 for shift in (16, 8, 0)]
            data[name] = {'actual':actual, 'expected':expected, 'pass':actual == expected}
        results['pixels'][label] = data
        return all(item['pass'] for item in data.values())

    def command(mode):
        subprocess.run(['wine', str(args.command_probe.resolve()), mode], env=env, check=True,
                       stdout=logs[-1], stderr=subprocess.STDOUT, timeout=10)

    try:
        with tempfile.TemporaryDirectory(prefix='ghub-window-test-') as directory:
            temporary = Path(directory)
            config = temporary / 'config'
            config.mkdir()
            (config / 'kwinrc').write_text('[Compositing]\nEnabled=true\nOpenGLIsUnsafe=false\n'
                '[Plugins]\nblurEnabled=false\ncontrastEnabled=false\nfadingpopupsEnabled=false\n'
                'fadeEnabled=false\nslidingpopupsEnabled=false\n[org.kde.kdecoration2]\nNoPlugin=true\n'
                '[KDE]\nAnimationDurationFactor=0\n')
            env.update(XDG_CONFIG_HOME=str(config), XDG_DATA_HOME=str(temporary / 'data'),
                       XDG_CACHE_HOME=str(temporary / 'cache'), XDG_RUNTIME_DIR=str(temporary), QT_QPA_PLATFORM='xcb')
            xserver = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1600x1100x24', '-nolisten', 'tcp'],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with selectors.DefaultSelector() as selector:
                selector.register(xserver.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError('Xvfb startup timed out')
            number = xserver.stdout.readline().decode().strip()
            if not number.isdecimal():
                raise RuntimeError('Invalid Xvfb display')
            env['DISPLAY'] = ':' + number
            results['display'] = env['DISPLAY']
            bus = temporary / 'bus.conf'
            bus.write_text('<busconfig><type>session</type><listen>unix:tmpdir=' + str(temporary) +
                '</listen><policy context="default"><allow send_destination="*"/>'
                '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
            logs.append((out / 'kwin.log').open('w'))
            wm = subprocess.Popen(['dbus-run-session', '--config-file', str(bus), '--', 'kwin_x11', '--replace'],
                                  env=dict(env, LIBGL_ALWAYS_SOFTWARE='1', KWIN_COMPOSE='O2'),
                                  stdout=logs[-1], stderr=subprocess.STDOUT, start_new_session=True)
            capture = Capture(env['DISPLAY'])
            for _ in range(100):
                if capture.ready():
                    break
                if wm.poll() is not None:
                    raise RuntimeError('Private KWin exited')
                time.sleep(.1)
            else:
                raise RuntimeError('Private compositor startup timed out')
            logs.append((out / 'wine.log').open('w'))
            argv = ['wine', str(args.fixture.resolve() / 'lghub.exe'), '--disable-gpu', '--enable-logging',
                    '--log-file=Z:' + str(out / 'electron.log').replace('/', '\\')]
            if not args.allow_software_rasterizer:
                argv.append('--disable-software-rasterizer')
            results['command'] = argv
            app = subprocess.Popen(argv, env=env, cwd=args.fixture, stdout=logs[-1], stderr=subprocess.STDOUT)
            window = None
            for _ in range(120):
                found = subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', '^GHUB Window Regression$'],
                                       env=env, capture_output=True, text=True, timeout=5)
                if found.returncode == 0 and found.stdout.strip():
                    window = found.stdout.splitlines()[0]
                    if sample(window, 'initial'):
                        break
                if app.poll() is not None:
                    raise RuntimeError('Fixture exited before rendering')
                time.sleep(.25)
            else:
                raise RuntimeError('Fixture did not paint the initial frame')
            xdo('windowactivate', '--sync', window)
            g = geometry(window)
            xdo('mousemove', '--sync', g['X']+350, g['Y']+350)
            xdo('click', 1)
            time.sleep(.3)
            for index in range(4):
                g = geometry(window)
                direction = 1 if index % 2 == 0 else -1
                # First stay inside the caption so Chromium begins its deferred
                # native move loop before crossing into the client area.
                xdo('mousemove', '--sync', g['X']+350, g['Y']+30)
                xdo('mousedown', 1)
                try:
                    time.sleep(.1)
                    for dx, dy in [(20,0), (60,30), (120,70)]:
                        xdo('mousemove', '--sync', g['X']+350+direction*dx, g['Y']+30+direction*dy)
                        time.sleep(.2)
                finally:
                    xdo('mouseup', 1)
                time.sleep(.5)
                released = geometry(window)
                xdo('mousemove', '--sync', released['X']+550, released['Y']+230)
                time.sleep(.5)
                final = geometry(window)
                moved = any(released[k] != g[k] for k in ('X', 'Y'))
                settled = released == final
                results['drags'].append({'before':g, 'released_geometry':released, 'after_pointer_motion':final,
                                         'moved':moved, 'released':settled, 'pass':moved and settled})
                sample(window, 'drag-' + str(index))
                if not (moved and settled):
                    raise RuntimeError('Caption drag did not start and release correctly')
            before = geometry(window)
            command('stale-mouse')
            time.sleep(.3)
            xdo('mousemove', '--sync', before['X']+600, before['Y']+260)
            time.sleep(.5)
            results['stale_mouse_ignored'] = geometry(window) == before
            if not results['stale_mouse_ignored']:
                raise RuntimeError('Released mouse command started a fresh grab')
            for mode in ('keyboard-move', 'keyboard-size'):
                before = geometry(window)
                command(mode)
                time.sleep(.3)
                for key in ('Right', 'Right', 'Down', 'Down', 'Return'):
                    xdo('key', key)
                    time.sleep(.15)
                released = geometry(window)
                xdo('mousemove', '--sync', released['X']+600, released['Y']+260)
                time.sleep(.3)
                fields = ('X','Y') if mode == 'keyboard-move' else ('WIDTH','HEIGHT')
                moved = any(before[k] != released[k] for k in fields)
                results['keyboard'][mode] = {'before':before, 'after':released,
                    'pass':moved and geometry(window) == released}
                sample(window, mode)
            xdo('windowminimize', window)
            time.sleep(.4)
            xdo('windowmap', window)
            xdo('windowactivate', '--sync', window)
            time.sleep(.6)
            sample(window, 'restore')
            results['pixel_pass'] = sum(p['pass'] for stage in results['pixels'].values() for p in stage.values())
            results['pixel_total'] = sum(len(stage) for stage in results['pixels'].values())
            results['pass'] = (results['pixel_pass'] == results['pixel_total'] and
                               all(item['pass'] for item in results['keyboard'].values()))
    except Exception as error:
        results.update({'pass':False, 'error':str(error)})
    finally:
        (out / 'result.json').write_text(json.dumps(results, indent=2) + '\n')
        if capture:
            capture.close()
        if app:
            subprocess.run(['wineserver', '-k'], env=env, timeout=15, check=False)
            subprocess.run(['wineserver', '-w'], env=env, timeout=15, check=False)
            app.wait(timeout=10)
        if wm:
            try:
                os.killpg(wm.pid, signal.SIGTERM)
                wm.wait(timeout=10)
            except ProcessLookupError:
                pass
        if xserver:
            xserver.terminate()
            xserver.wait(timeout=10)
        for log in logs:
            log.close()
    print(json.dumps({'pass':results.get('pass'), 'error':results.get('error'), 'results':str(out / 'result.json')}))
    return 0 if results.get('pass') else 1


if __name__ == '__main__':
    raise SystemExit(main())
