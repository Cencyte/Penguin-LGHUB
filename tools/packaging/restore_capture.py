import ctypes
import json
import os
import signal
import socket
import re
import desktop_runtime as desktop
import time
from pathlib import Path
from run_dcomp_tests import Capture
from restore_timing import sample_restore


def isolated_guard():
    private = Path('/tmp/penguin-wayland-launcher-probe')
    if (socket.if_nameindex() != [(1, 'lo')] or Path('/dev/input').exists()
            or list(Path('/dev').glob('hidraw*')) or not (private / 'control.json').is_file()
            or os.environ.get('XDG_RUNTIME_DIR') != str(private / 'run')):
        raise RuntimeError('Restore testing requires the private compositor/device/network namespace')


def reserve_panel(tray, screen_height, panel_height):
    """Give private KWin a bottom panel strut, like the live Plasma work area."""
    isolated_guard()
    if not 0 < panel_height < screen_height:
        raise ValueError('Invalid private panel height')
    x, d = tray.x, tray.display
    x.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    window = x.XCreateSimpleWindow(d, x.XDefaultRootWindow(d), 0, screen_height-panel_height,
                                   1280, panel_height, 0, 0, 0)
    class SizeHints(ctypes.Structure):
        _fields_ = [('flags', ctypes.c_long), ('values', ctypes.c_int * 17)]
    # Without explicit position hints KWin centers this test dock, obscuring
    # the application's middle and invalidating the compositor comparison.
    hints = SizeHints(3, (ctypes.c_int * 17)(0, screen_height-panel_height, 1280, panel_height))
    x.XSetWMNormalHints.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(SizeHints)]
    x.XSetWMNormalHints(d, window, ctypes.byref(hints))
    def prop(name, kind, values):
        values = (ctypes.c_ulong * len(values))(*values)
        x.XChangeProperty(d, window, x.XInternAtom(d, name.encode(), 0),
                          x.XInternAtom(d, kind.encode(), 0), 32, 0, values, len(values))
    prop('_NET_WM_WINDOW_TYPE', 'ATOM', [x.XInternAtom(d, b'_NET_WM_WINDOW_TYPE_DOCK', 0)])
    prop('_NET_WM_STRUT_PARTIAL', 'CARDINAL', [0,0,0,panel_height,0,0,0,0,0,0,0,1279])
    prop('_NET_WM_STRUT', 'CARDINAL', [0,0,0,panel_height])
    x.XMapWindow(d, window)
    x.XSync(d, 0)
    time.sleep(.5)
    x.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong)]
    xx, yy, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
    x.XTranslateCoordinates(d, window, x.XDefaultRootWindow(d), 0, 0,
                            ctypes.byref(xx), ctypes.byref(yy), ctypes.byref(child))
    if (xx.value, yy.value) != (0, screen_height-panel_height):
        raise RuntimeError('Private panel placement invalid: '+str((xx.value, yy.value)))
    return window

class ClientMessage(ctypes.Structure):
    _fields_=[('type',ctypes.c_int),('serial',ctypes.c_ulong),('send_event',ctypes.c_int),
              ('display',ctypes.c_void_p),('window',ctypes.c_ulong),('message_type',ctypes.c_ulong),
              ('format',ctypes.c_int),('data',ctypes.c_long*5)]
class XEvent(ctypes.Union):
    _fields_=[('client',ClientMessage),('pad',ctypes.c_long*24)]


def compositor_pixels(output, label, capture, xid, width, height):
    """Read only the isolated KWin composition; the host bus is unavailable."""
    isolated_guard()
    import dbus
    x, d = capture.x, capture.display
    x.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong)]
    xx, yy, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
    if not x.XTranslateCoordinates(d, xid, capture.root, 0, 0, ctypes.byref(xx), ctypes.byref(yy), ctypes.byref(child)):
        raise RuntimeError('Private window coordinates unavailable')
    bus = dbus.SessionBus(private=True)
    fd = os.memfd_create('penguin-private-compositor', os.MFD_CLOEXEC)
    try:
        api = dbus.Interface(bus.get_object('org.kde.KWin.ScreenShot2', '/org/kde/KWin/ScreenShot2'),
                             'org.kde.KWin.ScreenShot2')
        info = api.CaptureArea(xx.value, yy.value, width, height, dbus.Dictionary({}, signature='sv'),
                               dbus.types.UnixFd(fd), timeout=15)
        w, h, stride = (int(info[k]) for k in ('width', 'height', 'stride'))
        if int(info['format']) not in (4, 5, 6) or (w, h) != (width, height):
            raise RuntimeError('Unexpected private compositor pixel format/scale')
        deadline = time.monotonic()+15
        while os.fstat(fd).st_size < stride*h:
            if time.monotonic()>deadline: raise RuntimeError('Private compositor capture timed out')
            time.sleep(.02)
        raw = os.pread(fd, stride*h, 0)
        pixels = bytearray()
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                p=y*stride+x*4
                pixels.extend((raw[p+2], raw[p+1], raw[p]))
        (output/(label+'-composited.ppm')).write_bytes(f'P6\n{(w+1)//2} {(h+1)//2}\n255\n'.encode()+pixels)
        return pixels
    finally:
        os.close(fd)
        bus.close()


def compare_pixels(reference, pixels):
    marked = [i for i in range(0, len(reference), 3) if max(reference[i:i+3]) > 8]
    matched = sum(max(abs(pixels[i+j]-reference[i+j]) for j in range(3)) < 32 for i in marked) if len(reference)==len(pixels) else 0
    nonblack = sum(max(pixels[i:i+3]) > 8 for i in range(0, len(pixels), 3))
    fraction = matched/max(1, len(marked))
    return {'reference_match_fraction': fraction, 'nonblack': nonblack,
            'passed': fraction > .9 and nonblack < len(marked)*1.25}


def windows_state(install, action, label):
    request = {'action': action, 'label': label}
    temporary = install/'window-request.new'
    temporary.write_text(json.dumps(request))
    temporary.replace(install/'window-request.json')
    deadline = time.monotonic()+20
    while True:
        try: response = json.loads((install/'window-response.json').read_text())
        except FileNotFoundError: response = {}
        if response.get('request') == request:
            if response['exit']:
                raise RuntimeError('Private Windows probe failed: '+response['output'])
            match = re.search(r'STATE iconic=(\d) visible=(\d) size=(\d+)x(\d+)', response['output'])
            if not match: raise RuntimeError('Private Windows probe returned no state')
            return dict(zip(('iconic', 'visible', 'width', 'height'), map(int, match.groups())))
        if time.monotonic()>deadline: raise RuntimeError('Private Windows probe timed out')
        time.sleep(.1)

def wm_transition(capture, mode):
    isolated_guard()
    x=capture.x; d=capture.display
    x.XGetWindowProperty.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_long,ctypes.c_long,ctypes.c_int,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.POINTER(ctypes.c_int),ctypes.POINTER(ctypes.c_ulong),ctypes.POINTER(ctypes.c_ulong),ctypes.POINTER(ctypes.c_void_p)]
    x.XFetchName.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_void_p)]
    x.XFree.argtypes=[ctypes.c_void_p]
    x.XIconifyWindow.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int]
    x.XSendEvent.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_long,ctypes.POINTER(XEvent)]
    x.XSync.argtypes=[ctypes.c_void_p,ctypes.c_int]
    atom=x.XInternAtom(d,b'_NET_CLIENT_LIST',0)
    actual=ctypes.c_ulong();fmt=ctypes.c_int();count=ctypes.c_ulong();after=ctypes.c_ulong();data=ctypes.c_void_p()
    if x.XGetWindowProperty(d,capture.root,atom,0,1024,0,0,ctypes.byref(actual),ctypes.byref(fmt),ctypes.byref(count),ctypes.byref(after),ctypes.byref(data)) or fmt.value!=32:
        raise RuntimeError('No private window-manager client list')
    target=None
    try:
        for wid in ctypes.cast(data,ctypes.POINTER(ctypes.c_ulong))[:count.value]:
            name=ctypes.c_void_p()
            if x.XFetchName(d,wid,ctypes.byref(name)) and name.value:
                title=ctypes.string_at(name).decode(errors='replace');x.XFree(name)
                if title=='Logitech G HUB':target=wid
    finally:x.XFree(data)
    if not target:raise RuntimeError('No owned G HUB X11 window')
    if mode=='minimize':
        if not x.XIconifyWindow(d,target,0):raise RuntimeError('XIconifyWindow failed')
    elif mode=='restore':
        event=XEvent();event.client.type=33;event.client.display=d;event.client.window=target
        event.client.message_type=x.XInternAtom(d,b'_NET_ACTIVE_WINDOW',0);event.client.format=32
        event.client.data[0]=2
        if not x.XSendEvent(d,capture.root,0,(1<<20)|(1<<19),ctypes.byref(event)):raise RuntimeError('Pager activation failed')
    x.XSync(d,0)
    return target


def capture_request(output, snapshots, install):
    isolated_guard()
    try: label=(output/'capture-request').read_text()
    except FileNotFoundError: return
    if label in snapshots: return
    if label == 'relaunch':
        (output.parent/'no-debug').touch()
        for pid, exe in desktop.desktop.prefix_processes(install/'prefix'):
            if Path(exe.replace('\\','/')).name != 'lghub.exe': continue
            try:
                fd=os.pidfd_open(pid)
                try: signal.pidfd_send_signal(fd, signal.SIGTERM)
                finally: os.close(fd)
            except ProcessLookupError: pass
        time.sleep(3)
        config=install/'desktop.json'
        owner=desktop.desktop.session_owner(config, install/'session.json')
        desktop.desktop.control_request(install,owner,'app')
        time.sleep(12)
        commands = []
        for pid, exe in desktop.desktop.prefix_processes(install/'prefix'):
            if Path(exe.replace('\\', '/')).name == 'lghub.exe':
                try: commands.append(Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode())
                except FileNotFoundError: pass
        if not commands or any('--remote-debugging' in command for command in commands):
            raise RuntimeError('Debugger-free frontend restart failed')
        snapshots[label]={'debugger_enabled':False, 'frontend_process_count':len(commands)}
        (output/'capture-ack').write_text(label)
        return
    capture=Capture(os.environ['DISPLAY'])
    try:
        mode='minimize' if label.startswith('minimized-') else 'restore' if label.startswith('restored-') else 'query'
        control=json.loads((output.parent/'control.json').read_text())
        if mode=='minimize' and control.get('windows_minimize'):
            windows_state(install, 'minimize', label)
            xid=wm_transition(capture,'query')
        elif mode=='restore' and control.get('reopen'):
            config=install/'desktop.json'
            owner=desktop.desktop.session_owner(config,install/'session.json')
            desktop.desktop.control_request(install,owner,'app')
            xid=wm_transition(capture,'query')
        else:
            xid=wm_transition(capture,mode)
        timing = None
        if mode == 'restore' and control.get('restore_timing'):
            timing = sample_restore(capture, xid, output/'initial.ppm', isolated_guard)
        time.sleep(2)
        # Wine can replace its X window during restore/SetAlwaysOnTop.
        # Capture the current managed window, never the pre-restore XID.
        x=capture.x;d=capture.display
        deadline=time.monotonic()+15
        while True:
            xid=wm_transition(capture,'query')
            actual=ctypes.c_ulong();fmt=ctypes.c_int();count=ctypes.c_ulong();after=ctypes.c_ulong();data=ctypes.c_void_p()
            x.XGetWindowProperty(d,xid,x.XInternAtom(d,b'WM_STATE',0),0,10,0,0,ctypes.byref(actual),ctypes.byref(fmt),ctypes.byref(count),ctypes.byref(after),ctypes.byref(data))
            try: state=ctypes.cast(data,ctypes.POINTER(ctypes.c_ulong))[0] if count.value else None
            finally: x.XFree(data)
            if state == (3 if mode=='minimize' else 1): break
            if time.monotonic()>=deadline: raise RuntimeError('Unexpected WM_STATE '+str(state))
            time.sleep(.1)
        entry={'xid':xid,'wm_state':state}
        if timing is not None:
            entry['timing'] = timing
        if control.get('windows_minimize'):
            entry['win32'] = windows_state(install, 'query', label)
            if entry['win32']['iconic'] != (mode=='minimize'):
                snapshots[label] = entry
                (output/'snapshots.json').write_text(json.dumps(snapshots,indent=2)+'\n')
                raise RuntimeError('Xwayland and Win32 disagree about minimized state')
        if mode!='minimize':
            root=ctypes.c_ulong();gx=ctypes.c_int();gy=ctypes.c_int();w=ctypes.c_uint();h=ctypes.c_uint();border=ctypes.c_uint();depth=ctypes.c_uint()
            x.XGetGeometry.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.POINTER(ctypes.c_int),ctypes.POINTER(ctypes.c_int),ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint)]
            x.XGetGeometry(d,xid,ctypes.byref(root),ctypes.byref(gx),ctypes.byref(gy),ctypes.byref(w),ctypes.byref(h),ctypes.byref(border),ctypes.byref(depth))
            image=x.XGetImage(d,xid,0,0,w.value,h.value,ctypes.c_ulong(-1).value,2)
            if not image: raise RuntimeError('No window image')
            try:
                pixels=bytearray()
                for yy in range(0,h.value,2):
                    for xx in range(0,w.value,2):
                        v=x.XGetPixel(image,xx,yy);pixels.extend(((v>>16)&255,(v>>8)&255,v&255))
                (output/(label+'.ppm')).write_bytes(f'P6\n{(w.value+1)//2} {(h.value+1)//2}\n255\n'.encode()+pixels)
                entry.update(width=w.value,height=h.value,nonblack=sum(any(pixels[i+j]>8 for j in range(3)) for i in range(0,len(pixels),3)))
                if label == 'initial' and not 500 < entry['nonblack'] < len(pixels)//12:
                    raise RuntimeError('Initial Devices page is blank or unavailable')
                if label != 'initial':
                    reference=(output/'initial.ppm').read_bytes().split(b'\n',3)[3]
                    entry.update(compare_pixels(reference, pixels))

                if control.get('compositor_capture'):
                    composed = compositor_pixels(output, label, capture, xid, w.value, h.value)
                    entry['compositor'] = compare_pixels(pixels, composed)
                    if label == 'initial' and not entry['compositor']['passed']:
                        snapshots[label] = entry
                        (output/'snapshots.json').write_text(json.dumps(snapshots,indent=2)+'\n')
                        raise RuntimeError('Initial private KWin output differs from X window pixels')
                    if label != 'initial':
                        entry['passed'] = entry['passed'] and entry['compositor']['passed']

            finally:x.XDestroyImage(image)
        snapshots[label]=entry
        (output/'snapshots.json').write_text(json.dumps(snapshots,indent=2)+'\n')
        (output/'capture-ack').write_text(label)
    finally: capture.close()
