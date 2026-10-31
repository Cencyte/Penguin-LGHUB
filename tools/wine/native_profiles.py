"""Passive host foreground identity -> private G HUB typed-event mailbox.

No keyboard input, focus changes, public listeners, or settings edits. Run in
the supervisor's namespace: host /proc is shared by the packaged runtime.
"""
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import select
import shutil
import struct
import tempfile
import threading
import time
import subprocess

AGENT_SHA256 = '61772de356096af7cea986e2f9b3f42db69e6f410c4106609998c389fd755fc1'
EXPORT = b'PenguinProfileBridgeVersion'
HEADER = struct.Struct('<8s64sQQIII')
DESKTOP = r'C:\windows\system32\explorer.exe'
OWN = {'lghub.exe', 'lghub_agent.exe', 'lghub_system_tray.exe'}


def available(app):
    """Files are also covered by the launcher's existing pinned inventory."""
    return EXPORT in (app / 'mfsensorgroup.dll').read_bytes()


def wine_path(path, host_root=''):
    path = str(path)
    if not path.startswith('/') or '\x00' in path or '\\' in path:
        raise ValueError('Application must have an absolute Linux executable path')
    # pressure-vessel exports the host OS under /run/host; user files/mounts
    # retain their names. Prefer host OS paths even when container /usr exists.
    if host_root and path.startswith(('/usr/', '/bin/', '/sbin/', '/lib/', '/lib64/', '/etc/')):
        path = host_root + path
    return 'Z:' + path.replace('/', '\\')


def process_identity(pid, proc=Path('/proc')):
    """Reject foreign UIDs, exited processes and reuse during identity lookup."""
    p = proc / str(pid)
    if pid <= 0 or p.stat().st_uid != os.getuid():
        raise ValueError('Foreground process has a different owner')
    before = (p / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    executable = os.readlink(p / 'exe')
    arguments = (p / 'cmdline').read_bytes().split(b'\0')
    after = (p / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    if before != after or not executable.startswith('/') or executable.endswith(' (deleted)'):
        raise ValueError('Foreground process changed during lookup')
    # Wine's /proc/exe can be its loader; argv[0] names the actual PE file.
    first = os.fsdecode(arguments[0]) if arguments else ''
    if first and Path(first.replace('\\', '/')).name.lower() in OWN:
        # Agent-launched tray processes can use a C:\... argv[0]. Keep that
        # identity for suppression even when /proc/exe names Wine's ELF loader.
        return first.replace('\\', '/'), before
    if first.startswith('/') and first.lower().endswith('.exe'):
        executable = first
    return executable, before


class X11:
    def __init__(self):
        self.lib = x = C.CDLL('libX11.so.6')
        ulong = C.c_ulong
        signatures = {
            'XOpenDisplay': ([C.c_char_p], C.c_void_p),
            'XDefaultRootWindow': ([C.c_void_p], ulong),
            'XInternAtom': ([C.c_void_p, C.c_char_p, C.c_int], ulong),
            'XGetWindowProperty': ([C.c_void_p, ulong, ulong, C.c_long, C.c_long, C.c_int, ulong,
                                   C.POINTER(ulong), C.POINTER(C.c_int), C.POINTER(ulong),
                                   C.POINTER(ulong), C.POINTER(C.c_void_p)], C.c_int),
            'XFree': ([C.c_void_p], C.c_int),
            'XSelectInput': ([C.c_void_p, ulong, C.c_long], C.c_int),
            'XConnectionNumber': ([C.c_void_p], C.c_int),
            'XPending': ([C.c_void_p], C.c_int),
            'XNextEvent': ([C.c_void_p, C.c_void_p], C.c_int),
            'XFlush': ([C.c_void_p], C.c_int),
            'XCloseDisplay': ([C.c_void_p], C.c_int),
            'XGetTransientForHint': ([C.c_void_p, ulong, C.POINTER(ulong)], C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(x, name); fn.argtypes = args; fn.restype = result
        # A foreground window can disappear between reading its XID and PID.
        self.error_handler = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(lambda *_: 0)
        x.XSetErrorHandler.argtypes = [C.c_void_p]; x.XSetErrorHandler.restype = C.c_void_p
        self.previous_error_handler = x.XSetErrorHandler(self.error_handler)
        self.display = x.XOpenDisplay(None)
        if not self.display:
            x.XSetErrorHandler(self.previous_error_handler)
            raise RuntimeError('Cannot connect the profile observer to X11')
        self.root = x.XDefaultRootWindow(self.display)
        self.atoms = {name: x.XInternAtom(self.display, name.encode(), 0) for name in
                      ('_NET_ACTIVE_WINDOW', '_NET_WM_PID', '_NET_WM_WINDOW_TYPE',
                       '_NET_WM_WINDOW_TYPE_DESKTOP', '_NET_WM_WINDOW_TYPE_DOCK')}
        x.XSelectInput(self.display, self.root, 1 << 22)  # PropertyChangeMask
        x.XFlush(self.display)

    def values(self, window, name):
        actual = C.c_ulong(); fmt = C.c_int(); count = C.c_ulong(); remaining = C.c_ulong(); data = C.c_void_p()
        result = self.lib.XGetWindowProperty(self.display, window, self.atoms[name], 0, 16, 0, 0,
                                             C.byref(actual), C.byref(fmt), C.byref(count),
                                             C.byref(remaining), C.byref(data))
        try:
            if result or fmt.value != 32 or not data.value or count.value > 16:
                return []
            return list(C.cast(data, C.POINTER(C.c_ulong))[:count.value])
        finally:
            if data.value:
                self.lib.XFree(data)

    def foreground(self):
        active = self.values(self.root, '_NET_ACTIVE_WINDOW')
        window = active[0] if active else 0
        for _ in range(8):
            if not window:
                return 0, 0
            types = self.values(window, '_NET_WM_WINDOW_TYPE')
            if any(self.atoms[t] in types for t in ('_NET_WM_WINDOW_TYPE_DESKTOP', '_NET_WM_WINDOW_TYPE_DOCK')):
                return window, 0
            pid = self.values(window, '_NET_WM_PID')
            if pid:
                return window, pid[0]
            parent = C.c_ulong()
            if not self.lib.XGetTransientForHint(self.display, window, C.byref(parent)) or parent.value == window:
                return window, 0
            window = parent.value
        return window, 0

    def wait(self, timeout):
        select.select([self.lib.XConnectionNumber(self.display)], [], [], timeout)
        event = C.create_string_buffer(192)  # XEvent is 24 longs on 64-bit Xlib.
        while self.lib.XPending(self.display):
            self.lib.XNextEvent(self.display, event)

    def close(self):
        self.lib.XCloseDisplay(self.display)
        self.lib.XSetErrorHandler(self.previous_error_handler)


class Mailbox:
    def __init__(self, logs):
        self.directory = Path(tempfile.mkdtemp(prefix='profile-session-', dir=logs))
        self.path = self.directory / 'focus.bin'
        self.token = secrets.token_hex(32)
        self.sequence = 0
        self.current = None

    def environment(self):
        return {'PENGUIN_GHUB_FOCUS_FILE': wine_path(self.path), 'PENGUIN_GHUB_FOCUS_TOKEN': self.token}

    def write(self, path=None, stop=False):
        if path != self.current or stop:
            self.sequence += 1
            self.current = path
        if not self.sequence:
            return
        if self.sequence >= 0x7fffffff:
            raise RuntimeError('Profile session identity space exhausted')
        encoded = (path or '').encode('utf-8')
        if len(encoded) >= 4096 or b'\0' in encoded:
            raise ValueError('Executable path exceeds bridge protocol limit')
        # Synthetic IDs are session-local, outside normal Wine IDs. A new ID
        # per focus transition prevents PID reuse from matching stale apps.
        packet = HEADER.pack(b'PGFOCUS1', self.token.encode(), self.sequence, time.time_ns() // 1000000,
                             0x80000000 | self.sequence, int(stop), len(encoded)) + encoded
        temporary = self.path.with_suffix('.new')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(packet)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def status(self):
        try:
            token, sequence, message = self.path.with_suffix('.bin.status').read_text().strip().split(' ', 2)
            return (int(sequence), message) if token == self.token else None
        except (OSError, ValueError):
            return None

    def close(self):
        self.write(stop=True)

    def cleanup(self):
        shutil.rmtree(self.directory)


class ProfileObserver:
    def __init__(self, logs, host_root='', wayland=False):
        self.mailbox = Mailbox(logs)
        self.host_root = host_root
        self.stop_event = threading.Event()
        self.started = threading.Event()
        self.error = None
        self.thread = None
        self.ack_deadline = None
        self.wayland = wayland
        self.closed = False
        self.source = None

    def run(self):
        x = None
        try:
            if self.wayland:
                from profile_kwin import KWin
                x = KWin(self.mailbox.directory)
            else:
                x = X11()
            self.source = x
            self.started.set()
            candidate = None; since = 0; last_heartbeat = 0
            while not self.stop_event.is_set():
                foreground = x.foreground()
                if foreground is None:
                    candidate = None
                    x.wait(.1)
                    continue
                window, pid = foreground
                try:
                    identity = process_identity(pid) if pid else (None, None)
                    executable, _ = identity
                    own = executable and Path(executable).name.lower() in OWN
                    path = None if own else (wine_path(executable, self.host_root) if executable else DESKTOP)
                except PermissionError:
                    # A sandbox or privileged application is unidentifiable,
                    # not proof that the user activated Desktop.
                    candidate = None
                    x.wait(.1)
                    continue
                except (OSError, ValueError):
                    identity = (None, None); path = DESKTOP
                now = time.monotonic()
                key = (window, pid, identity, path)
                if key != candidate:
                    candidate = key; since = now
                # Avoid selecting Desktop during short focus/decoration gaps.
                if now - since >= .08 and path is not None:
                    changed = path != self.mailbox.current
                    if changed or now - last_heartbeat >= 1:
                        self.mailbox.write(path)
                        if changed:
                            print('Profile focus:', self.mailbox.sequence, path, flush=True)
                        last_heartbeat = now
                x.wait(.1)
        except Exception as error:
            self.error = error
            self.started.set()
        finally:
            if x:
                x.close()

    def start(self):
        self.ack_deadline = time.monotonic() + 10
        self.thread = threading.Thread(target=self.run, name='ghub-native-profile-observer', daemon=True)
        self.thread.start()
        if not self.started.wait(5):
            raise RuntimeError('Native profile observer did not start')
        self.check()

    def check(self):
        if self.error:
            raise RuntimeError('Native profile observer failed: ' + str(self.error))
        status = self.mailbox.status()
        if status and status[1] == 'unsupported-agent':
            raise RuntimeError('Native profiles require the pinned G HUB agent version')
        if status and status[1] in ('ready', 'accepted'):
            self.ack_deadline = None
        elif self.ack_deadline is not None and time.monotonic() > self.ack_deadline:
            raise RuntimeError('The G HUB profile adapter did not acknowledge startup')

    def close(self):
        if self.closed:
            return
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                raise RuntimeError('Native profile observer did not stop')
        self.mailbox.close()
        self.closed = True


def prepare(config, root, *, host=False):
    """Resolve the backend on the host; child supervisor uses only the mailbox."""
    if config.get('native_profiles') != 1:
        return None
    app = root / 'prefix/drive_c/Program Files/LGHUB'
    with (app / 'lghub_agent.exe').open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != AGENT_SHA256:
            raise RuntimeError('Native profiles require the pinned G HUB agent version')
    if not available(app):
        raise RuntimeError('Native profile bridge DLL is missing; rebuild the compatibility runtime')
    if not host and os.environ.get('PENGUIN_GHUB_HOST_PROFILE_DIR'):
        return RemoteObserver(root)
    if not host and config.get('kind') == 'penguin-desktop-runtime':
        raise RuntimeError('Packaged profile switching requires the host-side observer')
    host_root = '/run/host' if config.get('kind') == 'penguin-desktop-runtime' else ''
    wayland = os.environ.get('XDG_SESSION_TYPE') == 'wayland' or bool(os.environ.get('WAYLAND_DISPLAY'))
    return ProfileObserver(root / 'logs', host_root, wayland=wayland)


def write_control(path, token, **fields):
    data = dict(fields, token=token, timestamp=time.monotonic())
    temporary = path.with_suffix('.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_control(path, token):
    try:
        if path.is_symlink() or path.stat().st_size > 8192:
            return None
        value = json.loads(path.read_text())
        if isinstance(value, dict) and value.get('token') == token:
            return value
    except (OSError, ValueError):
        pass
    return None


class MailboxView(Mailbox):
    """Child has no ownership of the host observer's mailbox lifetime."""
    def __init__(self, directory, token):
        self.directory = directory; self.path = directory/'focus.bin'; self.token = token

    def cleanup(self):
        pass


class RemoteObserver:
    def __init__(self, root):
        directory = Path(os.environ['PENGUIN_GHUB_HOST_PROFILE_DIR'])
        token = os.environ.get('PENGUIN_GHUB_FOCUS_TOKEN', '')
        if (not directory.is_absolute() or directory.is_symlink()
                or directory.resolve().parent != (root/'logs').resolve()
                or not directory.name.startswith('profile-session-')
                or directory.stat().st_uid != os.getuid()
                or directory.stat().st_mode & 0o077
                or not re.fullmatch('[0-9a-f]{64}', token)):
            raise RuntimeError('Invalid host profile observer session')
        self.mailbox = MailboxView(directory, token)
        self.started = False

    def request(self, action):
        write_control(self.mailbox.directory/'request.json', self.mailbox.token, action=action)

    def start(self):
        self.request('start')
        self.started = True
        self.check()

    def check(self):
        state = read_control(self.mailbox.directory/'host.json', self.mailbox.token)
        if not state or not 0 <= time.monotonic()-state.get('timestamp', 0) < 10:
            raise RuntimeError('Host profile observer stopped responding')
        if state.get('error'):
            raise RuntimeError('Host profile observer: ' + state['error'])
        if self.started and state.get('stopped'):
            raise RuntimeError('Host profile observer stopped unexpectedly')

    def close(self):
        self.request('stop')
        end = time.monotonic()+4
        while time.monotonic() < end:
            state = read_control(self.mailbox.directory/'host.json', self.mailbox.token)
            if state and state.get('stopped'):
                status = self.mailbox.status()
                if status and status[1] in ('stopped', 'unsupported-agent'):
                    return
            time.sleep(.05)


class HostSession:
    """Parent-side observer lifecycle shared by legacy and packaged launchers."""
    def __init__(self, config, root):
        self.observer = prepare(config, root, host=True)
        self.error = None
        if self.observer:
            self.poll()

    def environment(self):
        if not self.observer:
            return {}
        return dict(self.observer.mailbox.environment(),
                    PENGUIN_GHUB_HOST_PROFILE_DIR=str(self.observer.mailbox.directory))

    def poll(self):
        observer = self.observer
        if not observer:
            return
        box = observer.mailbox
        request = read_control(box.directory/'request.json', box.token)
        try:
            if request and request.get('action') == 'stop':
                observer.close()
            elif request and request.get('action') == 'start' and not observer.closed:
                if observer.thread is None and self.error is None:
                    observer.start()
                if self.error is None:
                    observer.check()
        except (OSError, ValueError, RuntimeError) as error:
            self.error = str(error)
        write_control(box.directory/'host.json', box.token,
                      error=self.error, stopped=observer.closed)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.observer:
            try:
                self.observer.close()
            finally:
                self.observer.mailbox.cleanup()


def run_host_session(config, root, command, output):
    with HostSession(config, root) as profiles:
        env = dict(os.environ, **profiles.environment())
        with subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT) as child:
            while child.poll() is None:
                profiles.poll()
                time.sleep(.2)
            return child.returncode
