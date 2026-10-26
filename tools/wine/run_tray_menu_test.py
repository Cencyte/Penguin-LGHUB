#!/usr/bin/env python3
"""Exercise the native tray on private software X11/KWin with no network or hardware.

Requires a stopped disposable prepared installation, and a fresh network
namespace containing only loopback. Never obtains the user's display or bus.
"""
import argparse
import ctypes
import json
import os
import re
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time

from run_dcomp_tests import Capture
from tested_desktop_launch import check, prefix_processes


class CursorImage(ctypes.Structure):
    _fields_ = [('x',ctypes.c_short),('y',ctypes.c_short),('width',ctypes.c_ushort),
                ('height',ctypes.c_ushort),('xhot',ctypes.c_ushort),('yhot',ctypes.c_ushort),
                ('serial',ctypes.c_ulong),('pixels',ctypes.POINTER(ctypes.c_ulong))]


def cursor_sample(capture):
    fixes = ctypes.CDLL('libXfixes.so.3')
    fixes.XFixesGetCursorImage.argtypes = [ctypes.c_void_p]
    fixes.XFixesGetCursorImage.restype = ctypes.POINTER(CursorImage)
    capture.x.XFree.argtypes = [ctypes.c_void_p]
    pointer = fixes.XFixesGetCursorImage(capture.display)
    if not pointer: raise RuntimeError('No private-display cursor image')
    try:
        cursor = pointer.contents
        visible = sum(bool(cursor.pixels[i] & 0xff000000) for i in range(cursor.width*cursor.height))
        return dict(x=cursor.x,y=cursor.y,width=cursor.width,height=cursor.height,visible_pixels=visible)
    finally:
        capture.x.XFree(pointer)


def menu_content(capture, x, y, width, height):
    """Reject empty white windows: require dark text in each of five menu rows.

    This probe has no devices, so the native menu contains five fixed rows.
    Inspect the composed image, not a Wine/GDI backing surface.
    """
    if not (0 <= x < x+width <= capture.width and 0 <= y < y+height <= capture.height and height >= 150):
        return []
    image = capture.x.XGetImage(capture.display, capture.overlay, x, y, width, height,
                               ctypes.c_ulong(-1).value, 2)
    if not image: raise RuntimeError('No private menu image')
    try:
        counts = []
        for center in (24, 60, 96, 132, height-22):
            count = 0
            for py in range(center-7, center+8):
                for px in range(50, min(width, 160)):
                    pixel = capture.x.XGetPixel(image, px, py)
                    count += all(((pixel >> shift) & 255) < 160 for shift in (16,8,0))
            counts.append(count)
        return counts
    finally:
        capture.x.XDestroyImage(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--probe', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--move-window', action='store_true')
    parser.add_argument('--placement-only', action='store_true',
                        help='Validate placement, hover survival and dismissal; explicitly exclude menu actions')
    parser.add_argument('--surface-input', action='store_true',help='Check transparent margin and actual X cursor')
    parser.add_argument('--solid-background', action='store_true',
                        help='Require the solid acrylic fallback instead of a transparent margin')
    parser.add_argument('--two-monitors', action='store_true', help='Owned two-output RandR test')
    parser.add_argument('--x11-input', action='store_true',
                        help='Deliver clicks through the owned X server, with a held-button interval')
    parser.add_argument('--vertical-monitors', action='store_true',
                        help='Two 1080p heads with the lower head primary, matching the workstation')
    parser.add_argument('--plasma-tray', action='store_true',
                        help='Open via the installed KDE XEmbed/SNI bridge on a private bus')
    parser.add_argument('--navigation', action='store_true',
                        help='Verify each navigation row reopens the closed frontend')
    args = parser.parse_args()
    if args.navigation and args.placement_only:
        parser.error('--navigation cannot be combined with --placement-only')
    if args.vertical_monitors: args.two_monitors = True
    args.probe = args.probe.resolve(strict=True)
    root = args.root.resolve(strict=True)
    prefix = root / 'prefix'
    config = json.loads((root / 'launch.json').read_text())
    check(config, root)
    host_net = subprocess.check_output(['sudo','-n','readlink','/proc/1/ns/net'],text=True).strip()
    overrides = config['environment'].get('WINEDLLOVERRIDES','')
    prerequisites = {'private_network':os.readlink('/proc/self/ns/net') != host_net,
                     'stopped':not prefix_processes(prefix),
                     'disposable':(prefix / '.overlay-trace-disposable').is_file(),
                     'frozen':bool(config.get('freeze_updates')),
                     'hardware_disabled':all(item in overrides for item in ('winebus.sys=d','wineusb.sys=d'))}
    if not all(prerequisites.values()):
        parser.error('Unsatisfied prerequisites: '+json.dumps(prerequisites))
    args.output = args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=False)
    command = ['python3',str(root / 'runner/tested_desktop_launch.py'),'--config',str(root/'launch.json')]
    wm = manager = probe = capture = bus_process = watcher = tray_proxy = None
    result = {'network_isolated':True, 'live_desktop_touched':False,
              'scope':'placement-hover-dismissal' if args.placement_only else
                      'placement-hover-dismissal-navigation-close' if args.navigation else
                      'placement-hover-dismissal-close',
              'menu_actions_tested':not args.placement_only}
    probe_args = (['--move-window'] if args.move_window else []) + (['--placement-only'] if args.placement_only else [])
    if args.surface_input: probe_args += ['--surface-input']
    if args.solid_background: probe_args += ['--solid-background']
    if args.two_monitors: probe_args += ['--two-monitors']
    if args.x11_input: probe_args += ['--x11-input']
    if args.plasma_tray: probe_args += ['--plasma-tray']
    if args.navigation: probe_args += ['--navigation']
    result['surface_input_tested'] = args.surface_input
    result['solid_background'] = args.solid_background
    result['two_monitors'] = args.two_monitors
    result['x11_input'] = args.x11_input
    result['vertical_monitors'] = args.vertical_monitors
    result['plasma_tray'] = args.plasma_tray
    result['navigation'] = args.navigation
    with tempfile.TemporaryDirectory(prefix='ghub-menu-') as temp:
        temp = Path(temp)
        # Wine's no-RandR settings handler collapses Xinerama monitor positions.
        # Use actual independent RandR outputs, with automatic device attachment off.
        xcommand = ['/usr/lib/Xorg','-config',str(Path(__file__).with_name('private_two_monitor_xorg.conf').resolve()),
                    '-configdir',str(temp),'-logfile',str(args.output/'xorg.log'),
                    '-displayfd','1','-noreset','-novtswitch','-sharevts','-nolisten','tcp'] if args.two_monitors else \
                   ['Xvfb','-displayfd','1','-screen','0','1024x768x24','-nolisten','tcp']
        xvfb = subprocess.Popen(xcommand,
                                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        try:
            with selectors.DefaultSelector() as s:
                s.register(xvfb.stdout,selectors.EVENT_READ)
                if not s.select(10): raise TimeoutError('Xvfb startup')
            display = xvfb.stdout.readline().decode().strip()
            if not display.isdecimal(): raise RuntimeError('invalid owned display')
            if args.two_monitors:
                xenv = dict(os.environ,DISPLAY=':'+display)
                modes = ['1920x1080','1920x1080'] if args.vertical_monitors else ['1024x768','800x600']
                positions = ['0x1080','0x0'] if args.vertical_monitors else ['0x0','1024x0']
                subprocess.run(['xrandr','--addmode','DUMMY1',modes[1]],env=xenv,check=True,timeout=10)
                subprocess.run(['xrandr','--output','DUMMY0','--mode',modes[0],'--pos',positions[0],
                    '--primary','--output','DUMMY1','--mode',modes[1],'--pos',positions[1]],
                    env=xenv,check=True,timeout=10)
                (args.output/'x11-monitors.txt').write_text(subprocess.check_output(
                    ['xrandr','--listmonitors'],env=xenv,text=True,timeout=10))
            cfg = temp/'config'; cfg.mkdir()
            (cfg/'kwinrc').write_text('[Compositing]\nEnabled=true\nOpenGLIsUnsafe=false\n'
                '[Plugins]\nblurEnabled=false\ncontrastEnabled=false\nfadingpopupsEnabled=false\n'
                'fadeEnabled=false\nslidingpopupsEnabled=false\n[org.kde.kdecoration2]\nNoPlugin=true\n'
                '[KDE]\nAnimationDurationFactor=0\n')
            env = dict(os.environ, DISPLAY=':'+display, GHUB_LAUNCHER_NO_DIALOG='1',
                       XDG_CONFIG_HOME=str(cfg),XDG_DATA_HOME=str(temp/'data'),
                       XDG_CACHE_HOME=str(temp/'cache'),XDG_RUNTIME_DIR=str(temp),QT_QPA_PLATFORM='xcb')
            for key in ('WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS'): env.pop(key,None)
            bus = temp/'bus.conf'
            bus.write_text('<busconfig><type>session</type><listen>unix:tmpdir='+str(temp)+
                '</listen><policy context="default"><allow send_destination="*"/>'
                '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
            if args.plasma_tray:
                import dbus
                bus_process = subprocess.Popen(['dbus-daemon','--nofork','--print-address=1',
                    '--config-file='+str(bus)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                with selectors.DefaultSelector() as selector:
                    selector.register(bus_process.stdout,selectors.EVENT_READ)
                    if not selector.select(10): raise TimeoutError('Private bus startup')
                address = bus_process.stdout.readline().decode().strip()
                if not address.startswith('unix:'): raise RuntimeError('Invalid private bus')
                env.update(DBUS_SESSION_BUS_ADDRESS=address, GHUB_TRAY_PRIVATE_LAB='1', XDG_SESSION_TYPE='x11')
                watcher = subprocess.Popen(['python3',str(Path(__file__).with_name('private_tray_watcher.py')),address],
                    env=env,stdout=subprocess.PIPE)
                with selectors.DefaultSelector() as selector:
                    selector.register(watcher.stdout,selectors.EVENT_READ)
                    if not selector.select(10) or watcher.stdout.readline().strip() != b'READY':
                        raise RuntimeError('Private SNI watcher startup')
                private_bus = dbus.bus.BusConnection(address)
                with (args.output/'xembedsniproxy.log').open('w') as log:
                    tray_proxy = subprocess.Popen(['xembedsniproxy'],env=env,stdout=log,stderr=subprocess.STDOUT)
            with (args.output/'kwin.log').open('w') as log:
                wm_command = ['kwin_x11','--replace'] if args.plasma_tray else \
                             ['dbus-run-session','--config-file',str(bus),'--','kwin_x11','--replace']
                wm = subprocess.Popen(wm_command,
                    env=dict(env,LIBGL_ALWAYS_SOFTWARE='1',KWIN_COMPOSE='O2'),stdout=log,stderr=subprocess.STDOUT,
                    start_new_session=True)
            capture = Capture(env['DISPLAY'])
            for _ in range(100):
                if capture.ready(): break
                if wm.poll() is not None: raise RuntimeError('KWin exited')
                time.sleep(.1)
            else: raise TimeoutError('KWin startup')
            started = time.time()
            with (args.output/'launcher.log').open('w') as log:
                manager = subprocess.Popen(command+['app'],env=env,stdout=log,stderr=subprocess.STDOUT)
            for _ in range(220):
                if manager.poll() is not None: raise RuntimeError('Launcher exited')
                snap = root/'logs/startup-snapshot.json'
                if snap.exists() and snap.stat().st_mtime >= started: break
                time.sleep(.5)
            else: raise TimeoutError('Agent startup')
            time.sleep(20)
            state = json.loads((root/'logs/session.json').read_text())
            ns = ['sudo','-n','nsenter','-t',str(state['pid']),'-m','--','setpriv',
                  '--reuid',str(os.getuid()),'--regid',str(os.getgid()),'--init-groups']
            with (args.output/'native-menu.log').open('w') as log, selectors.DefaultSelector() as s:
                probe = subprocess.Popen(ns+['env','WINEPREFIX='+str(prefix),'DISPLAY='+env['DISPLAY'],
                    'GHUB_TRAY_PRIVATE_LAB=1','WINEDEBUG=-all,err+all','wine',str(args.probe.resolve())]+probe_args,
                    env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                s.register(probe.stdout,selectors.EVENT_READ)
                pending = b''; deadline = time.monotonic()+300; sequence = 0
                origin_x = origin_y = 0
                while True:
                    if time.monotonic() > deadline: raise TimeoutError('Native menu test')
                    if not s.select(.25): continue
                    block = os.read(probe.stdout.fileno(),65536)
                    if not block: break
                    pending += block
                    while b'\n' in pending:
                        line,pending = pending.split(b'\n',1)
                        line = line.decode(errors='replace').rstrip('\r')
                        log.write(line+'\n'); log.flush()
                        match = re.fullmatch(r'SNI_OPEN (-?\d+) (-?\d+)',line)
                        if match:
                            if not args.plasma_tray: raise RuntimeError('Unexpected SNI request')
                            props = dbus.Interface(private_bus.get_object('org.kde.StatusNotifierWatcher',
                                '/StatusNotifierWatcher'), 'org.freedesktop.DBus.Properties')
                            items = props.Get('org.kde.StatusNotifierWatcher','RegisteredStatusNotifierItems')
                            if len(items) != 1: raise RuntimeError('Expected exactly one isolated tray item: '+str(items))
                            service, path = str(items[0]).split('/',1)
                            item = private_bus.get_object(service,'/'+path)
                            x,y = map(int,match.groups())
                            dbus.Interface(item,'org.kde.StatusNotifierItem').ContextMenu(
                                dbus.Int32(x-origin_x),dbus.Int32(y-origin_y),timeout=10)
                            log.write('SNI_CONTEXT_MENU '+str(items[0])+'\n'); log.flush()
                            probe.stdin.write(b'\n'); probe.stdin.flush()
                        match = re.fullmatch(r'DESKTOP (-?\d+) (-?\d+) (\d+) (\d+)',line)
                        if match:
                            origin_x, origin_y, width, height = map(int,match.groups())
                            if (width,height) != (capture.width,capture.height):
                                raise RuntimeError('Wine/X11 desktop geometry mismatch')
                            probe.stdin.write(b'\n'); probe.stdin.flush()
                        match = re.fullmatch(r'XCLICK (-?\d+) (-?\d+)',line)
                        if match:
                            if not args.x11_input: raise RuntimeError('Unexpected X input request')
                            # env always names the X server created above; never the host desktop.
                            x, y = map(int,match.groups())
                            x -= origin_x; y -= origin_y
                            subprocess.run(['xdotool','mousemove',str(x),str(y),
                                'sleep','0.15','mousedown','1','sleep','0.12','mouseup','1'],
                                env=env,check=True,timeout=10)
                            probe.stdin.write(b'\n'); probe.stdin.flush()
                        match = re.fullmatch(r'CAPTURE (-?\d+) (-?\d+) (\d+) (\d+) (\d+)',line)
                        if match:
                            x,y,*expected = map(int,match.groups())
                            actual = capture.pixel(x-origin_x,y-origin_y)
                            ok = all(abs(a-b)<=1 for a,b in zip(actual,expected))
                            log.write(f'XSERVER ({x},{y}) {actual} expected={expected} {"PASS" if ok else "FAIL"}\n')
                            log.flush(); probe.stdin.write(b'\n' if ok else b'!'); probe.stdin.flush()
                        if line.startswith('CURSOR '):
                            value = cursor_sample(capture)
                            log.write('XCURSOR '+json.dumps(value)+'\n'); log.flush()
                            probe.stdin.write(b'\n' if value['visible_pixels'] else b'!'); probe.stdin.flush()
                        match = re.fullmatch(r'CONTENT (-?\d+) (-?\d+) (\d+) (\d+)',line)
                        if match:
                            x,y,width,height = map(int,match.groups())
                            counts = menu_content(capture,x-origin_x,y-origin_y,width,height)
                            log.write('MENU_TEXT_PIXELS '+json.dumps(counts)+'\n'); log.flush()
                            probe.stdin.write(b'\n' if len(counts) == 5 and min(counts) >= 20
                                              and max(counts) < 825 else b'!')
                            probe.stdin.flush()
                        if line.startswith('CHECKPOINT '):
                            sequence += 1
                            name = f'{sequence:02d}-'+line.split()[1]
                            tree = subprocess.check_output(['xwininfo','-root','-tree'],env=env,text=True)
                            (args.output/(name+'.windows.txt')).write_text(tree)
                            xwd = args.output/(name+'.xwd')
                            subprocess.run(['xwd','-silent','-id',str(capture.overlay),'-out',str(xwd)],
                                           env=env,check=True,timeout=10)
                            subprocess.run(['magick',str(xwd),str(xwd.with_suffix('.png'))],check=True,timeout=10)
                            xwd.unlink()
                            probe.stdin.write(b'\n'); probe.stdin.flush()
                result['probe_exit'] = probe.wait(timeout=10)
        finally:
            if probe and probe.poll() is None: probe.kill(); probe.wait()
            if manager and manager.poll() is None:
                subprocess.run(command+['stop'],env=env,timeout=65)
                manager.wait(timeout=20)
            if capture: capture.close()
            if wm:
                if wm.poll() is None: os.killpg(wm.pid,signal.SIGTERM)
                wm.wait(timeout=10)
            for service in (tray_proxy,watcher,bus_process):
                if service:
                    service.terminate(); service.wait(timeout=10)
            xvfb.terminate(); xvfb.wait(timeout=10)
            (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
    return result.get('probe_exit',2)


if __name__ == '__main__': raise SystemExit(main())
