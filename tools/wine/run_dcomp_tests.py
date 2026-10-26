#!/usr/bin/env python3
"""Test the lab compositor on a private Xvfb display and stopped copied prefix.

Run inside run_private_build.py with the matched ntdll/D3D11/WineD3D builds.
--native-d2d supplies the user's Windows DLL; --framework tests the real bundled
FrameworkUdk wrappers. --composited additionally needs installed kwin_x11,
dbus-run-session, libX11 and libXcomposite. No packages or host DLLs are changed.
"""
import argparse
import ctypes
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time


class Capture:
    def __init__(self, display):
        self.x = ctypes.CDLL('libX11.so.6')
        self.c = ctypes.CDLL('libXcomposite.so.1')
        for lib, name, result, arguments in [
            (self.x, 'XOpenDisplay', ctypes.c_void_p, [ctypes.c_char_p]),
            (self.x, 'XDefaultRootWindow', ctypes.c_ulong, [ctypes.c_void_p]),
            (self.x, 'XDefaultScreen', ctypes.c_int, [ctypes.c_void_p]),
            (self.x, 'XDisplayWidth', ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            (self.x, 'XDisplayHeight', ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            (self.x, 'XInternAtom', ctypes.c_ulong, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]),
            (self.x, 'XGetSelectionOwner', ctypes.c_ulong, [ctypes.c_void_p, ctypes.c_ulong]),
            (self.x, 'XGetImage', ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                                ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]),
            (self.x, 'XGetPixel', ctypes.c_ulong, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
            (self.x, 'XDestroyImage', ctypes.c_int, [ctypes.c_void_p]),
            (self.x, 'XCloseDisplay', ctypes.c_int, [ctypes.c_void_p]),
            (self.c, 'XCompositeGetOverlayWindow', ctypes.c_ulong, [ctypes.c_void_p, ctypes.c_ulong]),
            (self.c, 'XCompositeReleaseOverlayWindow', None, [ctypes.c_void_p, ctypes.c_ulong]),
        ]:
            function = getattr(lib, name)
            function.restype, function.argtypes = result, arguments
        self.display = self.x.XOpenDisplay(display.encode())
        if not self.display:
            raise RuntimeError('Cannot open private display')
        self.root = self.x.XDefaultRootWindow(self.display)
        self.overlay = None
        screen = self.x.XDefaultScreen(self.display)
        self.width = self.x.XDisplayWidth(self.display, screen)
        self.height = self.x.XDisplayHeight(self.display, screen)

    def ready(self):
        atom = self.x.XInternAtom(self.display, b'_NET_WM_CM_S0', 0)
        if not self.x.XGetSelectionOwner(self.display, atom):
            return False
        # Read the composited output. Root-window GDI reads and xwd's
        # multi-visual reconstruction do not represent this final image.
        self.overlay = self.c.XCompositeGetOverlayWindow(self.display, self.root)
        return bool(self.overlay)

    def pixel(self, x, y):
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError('Sample outside private screen')
        image = self.x.XGetImage(self.display, self.overlay, x, y, 1, 1, ctypes.c_ulong(-1).value, 2)
        if not image:
            raise RuntimeError('XGetImage failed')
        try:
            value = self.x.XGetPixel(image, 0, 0)
            return [(value >> shift) & 255 for shift in (16, 8, 0)]
        finally:
            self.x.XDestroyImage(image)

    def close(self):
        # Closing this dedicated X connection releases its overlay reference.
        # Do not send a new request using a cached root XID during teardown:
        # nested multi-screen servers can replace roots as Wine restores modes.
        if self.display:
            self.x.XCloseDisplay(self.display)
            self.display = self.overlay = None


def run(command, env, log, capture=None):
    with log.open('w') as output, selectors.DefaultSelector() as selector:
        process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = b''
        deadline = time.monotonic() + 120
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'Test timed out: {log.name}')
                if not selector.select(0.25):
                    continue
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    if pending:
                        output.write(pending.decode(errors='replace'))
                    break
                pending += block
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    line = line.decode(errors='replace').rstrip('\r')
                    output.write(line + '\n')
                    match = re.fullmatch(r'CAPTURE (-?\d+) (-?\d+) (\d+) (\d+) (\d+)', line)
                    if match:
                        if capture is None:
                            raise RuntimeError('Unexpected capture request')
                        x, y, *expected = map(int, match.groups())
                        for attempt in range(20):
                            actual = capture.pixel(x, y)
                            ok = all(abs(a - b) <= 1 for a, b in zip(actual, expected))
                            if ok:
                                break
                            time.sleep(0.05)
                        output.write(f'XSERVER ({x},{y}) {actual} expected={expected} {"PASS" if ok else "FAIL"}\n')
                        process.stdin.write(b'\n' if ok else b'!')
                        process.stdin.flush()
                    output.flush()
            result = process.wait(timeout=10)
            print(f'{log.name}: exit {result}', flush=True)
            return result == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    parser.add_argument('--native-d2d', type=Path)
    parser.add_argument('--framework', action='store_true')
    parser.add_argument('--composited', action='store_true')
    parser.add_argument('--embedded-child', action='store_true',
                        help='also test below-child targets; requires --window-build-dir in outer runner')
    parser.add_argument('--input-binding', action='store_true',
                        help='also test retained input sinks; requires matched composition-build-dir')
    parser.add_argument('--input-only', action='store_true', help='run only the input scene / routing regression')
    parser.add_argument('--mouse-pointer', action='store_true', help='Exercise opt-in composition mouse-pointer samples')
    args = parser.parse_args()
    if args.embedded_child and not args.composited:
        parser.error('--embedded-child requires --composited for real output checks')
    build, prefix = args.build_dir.resolve(strict=True), args.prefix.resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    if prefix in (Path.home() / '.wine', repo / 'wine-lab/codex-run/prefix'):
        parser.error('Use a stopped lab copy, not the default/launcher prefix')
    if not (prefix / 'drive_c/Program Files/LGHUB/lghub_agent.exe').is_file():
        parser.error('A copied, installed G HUB lab prefix is required')
    marker = f'WINEPREFIX={prefix}'.encode()
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            command = (process / 'cmdline').read_bytes().split(b'\0')[0].lower()
            env = (process / 'environ').read_bytes().split(b'\0')
        except OSError:
            continue
        if marker in env and (b'wine' in command or command.endswith(b'.exe')):
            parser.error(f'Prefix already in use by PID {process.name}')
    args.log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(WINEPREFIX=str(prefix), WINEDEBUG='-all', WINEDLLOVERRIDES='winemenubuilder.exe=d;dcomp=n',
               GHUB_D3D11_PARTNER_PROBE='1', GHUB_COREMSG_PORT_ADAPTER='1', GHUB_COREMSG_FRAMEWORK_ADAPTER='1',
               WINE_GHUB_INPUT_ROUTING='1')
    if args.mouse_pointer: env['WINE_GHUB_POINTER_MOUSE'] = '1'
    else: env.pop('WINE_GHUB_POINTER_MOUSE', None)
    for name in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'GHUB_DCOMP_EXTERNAL_CAPTURE', 'LIBGL_ALWAYS_SOFTWARE'):
        env.pop(name, None)
    xserver = wm = capture = wm_log = None
    with tempfile.TemporaryDirectory(prefix='dcomp-test-') as directory:
        temporary = Path(directory)
        for name in ('dcomp.dll', 'dcomp_device_probe.exe', 'dcomp_render_test.exe', 'dcomp_clock_test.exe'):
            shutil.copy2(build / name, temporary / name)
        if args.native_d2d:
            shutil.copy2(args.native_d2d, temporary / 'd2d1.dll')
            env['WINEDLLOVERRIDES'] += ';d2d1=n'
        try:
            xserver = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1024x768x24', '-nolisten', 'tcp'],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with selectors.DefaultSelector() as selector:
                selector.register(xserver.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise TimeoutError('Private Xvfb did not start')
            number = xserver.stdout.readline().decode().strip()
            if not number.isdecimal():
                raise RuntimeError('Invalid private display number')
            env['DISPLAY'] = ':' + number
            if args.composited:
                config = temporary / 'config'
                config.mkdir()
                (config / 'kwinrc').write_text('[Compositing]\nEnabled=true\nOpenGLIsUnsafe=false\n'
                    '[Plugins]\nblurEnabled=false\ncontrastEnabled=false\nfadingpopupsEnabled=false\n'
                    'fadeEnabled=false\nslidingpopupsEnabled=false\n[org.kde.kdecoration2]\nNoPlugin=true\n'
                    '[KDE]\nAnimationDurationFactor=0\n')
                env.update(XDG_CONFIG_HOME=str(config), XDG_DATA_HOME=str(temporary / 'data'),
                           XDG_CACHE_HOME=str(temporary / 'cache'), XDG_RUNTIME_DIR=str(temporary), QT_QPA_PLATFORM='xcb')
                # No service directories: do not launch desktop portals or other
                # session services while testing a private window manager.
                bus = temporary / 'bus.conf'
                bus.write_text('<busconfig><type>session</type><listen>unix:tmpdir=' + str(temporary) +
                    '</listen><policy context="default"><allow send_destination="*"/>'
                    '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
                wm_log = (args.log_dir / 'kwin.log').open('w')
                wm_env = dict(env, LIBGL_ALWAYS_SOFTWARE='1', KWIN_COMPOSE='O2')
                wm = subprocess.Popen(['dbus-run-session', '--config-file', str(bus), '--', 'kwin_x11', '--replace'],
                                      env=wm_env, stdout=wm_log, stderr=subprocess.STDOUT, start_new_session=True)
                capture = Capture(env['DISPLAY'])
                for attempt in range(100):
                    if capture.ready():
                        break
                    if wm.poll() is not None:
                        raise RuntimeError('Private KWin exited')
                    time.sleep(0.1)
                else:
                    raise TimeoutError('Private compositor did not start')
            if args.input_only:
                ok = True
                if capture: env['GHUB_DCOMP_EXTERNAL_CAPTURE'] = '1'
            else:
                ok = run(['wine', str(temporary / 'dcomp_clock_test.exe')], env,
                         args.log_dir / 'clock.log')
                for abi in ('rs5', 'vb'):
                    command = ['wine', str(temporary / 'dcomp_device_probe.exe'), 'dcomp.dll', abi]
                    ok = run(command, env, args.log_dir / f'device-{abi}.log') and ok
                    if args.framework:
                        wrapper_env = dict(env, WINEDLLOVERRIDES=env['WINEDLLOVERRIDES'] + ';coremessaging=n')
                        framework = r'C:\Program Files\LGHUB\system_tray\Microsoft.Internal.FrameworkUdk.dll'
                        ok = run(command + [framework], wrapper_env, args.log_dir / f'framework-{abi}.log') and ok
                if capture:
                    env['GHUB_DCOMP_EXTERNAL_CAPTURE'] = '1'
                if args.framework:
                    env['GHUB_DCOMP_FRAMEWORK_PATH'] = r'C:\Program Files\LGHUB\system_tray\Microsoft.Internal.FrameworkUdk.dll'
                    env['WINEDLLOVERRIDES'] += ';coremessaging=n'
                command = ['wine', str(temporary / 'dcomp_render_test.exe')]
                if args.native_d2d:
                    command.append('--native-d2d')
                ok = run(command, env, args.log_dir / 'pixels.log', capture) and ok
            if args.input_binding or args.input_only:
                input_env = dict(env, WINE_GHUB_INPUT_SINK='1', WINE_GHUB_INPUT_DELEGATION='1')
                ok = run(['wine', str(temporary / 'dcomp_render_test.exe'), '--input-binding'], input_env,
                         args.log_dir / 'input-binding.log', capture) and ok
            if args.embedded_child:
                child_env = dict(env, WINE_GHUB_CHILD_COMPOSITION='1')
                ok = run(['wine', str(temporary / 'dcomp_render_test.exe'), '--embedded-child'], child_env,
                         args.log_dir / 'child-pixels.log', capture) and ok
            return 0 if ok else 1
        finally:
            if xserver:
                subprocess.run(['wineserver', '-k'], env=env, timeout=10, check=False)
                subprocess.run(['wineserver', '-w'], env=env, timeout=15, check=False)
            if capture:
                capture.close()
            if wm:
                try:
                    os.killpg(wm.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    wm.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(wm.pid, signal.SIGKILL)
                    wm.wait()
            if wm_log:
                wm_log.close()
            if xserver:
                xserver.terminate()
                xserver.wait(timeout=10)
                xserver.stdout.close()


if __name__ == '__main__':
    raise SystemExit(main())
