#!/usr/bin/env python3
"""User-operated frozen Wine test installation. Automated launches use Xvfb."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

SCRIPT = Path(__file__).resolve()


def register_frontend_protocol():
    r"""Install the native agent's deep-link route in this Wine prefix.

    Wine 10.20 resolves HKCR through HKLM\Software\Classes. Electron registers
    lghubapp in HKCU, which is insufficient for the agent's ShellExecuteExW.
    Keep the same rendering option as open_gui when a tray row reopens it.
    """
    key = r'HKLM\Software\Classes\lghubapp'
    command = r'"C:\Program Files\LGHUB\lghub.exe" --disable-gpu "%1"'
    for suffix, value, data in [('', ['/ve'], 'URL:lghubapp'),
                                ('', ['/v', 'URL Protocol'], ''),
                                (r'\shell\open\command', ['/ve'], command)]:
        result = subprocess.run(['wine', 'reg', 'add', key + suffix, *value,
                                 '/t', 'REG_SZ', '/d', data, '/f'], timeout=20,
                                stdout=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError(f'Cannot register the G HUB tray link handler (reg exit {result.returncode})')


def freeze_updates(prefix, logs):
    """Set the native pipeline preferences before Wine starts; preserve other keys.

    The caller holds launch.lock and has verified there are no prefix processes.
    Loader exclusion and the namespace's read-only application mount provide
    separate protection against a pending or manually requested installation.
    """
    path = prefix / 'drive_c/ProgramData/LGHUB/periodic_check.json'
    if not path.resolve().is_relative_to(prefix.resolve()):
        raise RuntimeError('Update preferences escape the test prefix')
    original = path.read_bytes() if path.exists() else None
    settings = json.loads(original) if original is not None else {'intervalInSeconds':86400}
    if not isinstance(settings, dict):
        raise RuntimeError('Invalid G HUB periodic update preferences')
    backup = logs / 'periodic_check.before-freeze.json'
    if original is not None and not backup.exists():
        with backup.open('xb') as output:
            output.write(original)
    settings.update(checkPeriodically=False, downloadAutomatically=False)
    if original is None or json.loads(original) != settings:
        temporary = path.with_name(path.name + '.freeze-new')
        with temporary.open('x') as output:
            json.dump(settings, output, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def prefix_processes(prefix):
    marker = ('WINEPREFIX=' + str(prefix)).encode()
    found = []
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            command = (process / 'cmdline').read_bytes().split(b'\0')[0].lower()
            env = (process / 'environ').read_bytes().split(b'\0')
        except OSError:
            continue
        if marker in env and (b'wine' in command or command.endswith(b'.exe')):
            found.append((int(process.name), os.fsdecode(command)))
    return found


def start_time(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def check(config, root, mounted=False):
    if config.get('tray_executable'):
        raise RuntimeError('The retired tray executable overlay must be removed; use host-window anchoring.')
    if config['schema'] != 1 or not (root / 'prefix/drive_c/Program Files/LGHUB/lghub_agent.exe').is_file():
        raise RuntimeError('Invalid frozen test installation')
    if config.get('frontend_archive'):
        if not config.get('freeze_updates') or config['frontend_archive'] not in config['sha256']:
            raise RuntimeError('Private frontend archive must be pinned with the update freeze enabled')
    if config.get('environment', {}).get('WINE_GHUB_HOST_KEYBOARD') == '1':
        build = config['builds'].get('--composition-build-dir')
        if not build or config['builds'].get('--x11-build-dir') != build:
            raise RuntimeError('Host keyboard output requires the matched Win32u and X11 build')
        for relative in ('ghub-host-keyboard-protocol', 'dlls/win32u/win32u.so', 'dlls/winex11.drv/winex11.so'):
            if str(Path(build)/relative) not in config['sha256']:
                raise RuntimeError('Host keyboard output component is not pinned: ' + relative)
    for relative, expected in config['sha256'].items():
        if mounted and config.get('frontend_archive') and relative == 'prefix/drive_c/Program Files/LGHUB/resources/app.asar':
            expected = config['sha256'][config['frontend_archive']]
        path = root / relative
        if not path.resolve().is_relative_to(root) or digest(path) != expected:
            raise RuntimeError(f'Tested component changed: {relative}')


def session_owner(config_path, state_path):
    """Identify this supervisor, not just a PID left in an old state file."""
    try:
        state = json.loads(state_path.read_text())
        process = Path('/proc') / str(int(state['pid']))
        cmd = (process / 'cmdline').read_bytes().split(b'\0')
        if (state['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                and process.stat().st_uid == os.getuid()
                and start_time(state['pid']) == state['start_time']
                and os.fsencode(SCRIPT) in cmd and os.fsencode(config_path) in cmd
                and b'_session' in cmd):
            return state
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def stop(config_path, state_path):
    state = session_owner(config_path, state_path)
    if state is None:
        return
    try:
        fd = os.pidfd_open(state['pid'])
        try:
            if session_owner(config_path, state_path) == state:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
        finally:
            os.close(fd)
    except (FileNotFoundError, ProcessLookupError):
        pass


def control_address(root):
    # Linux abstract sockets avoid the 108-byte filesystem-path limit and
    # disappear with their owner. The private runner shares our network ns.
    key = hashlib.sha256(os.fsencode(root.resolve())).hexdigest()[:32]
    return '\0ghub-tested-' + str(os.getuid()) + '-' + key


def control_request(root, owner, mode):
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
        client.settimeout(25)  # Startup may be inside a 15-second readiness probe.
        client.connect(control_address(root))
        pid, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if (pid != owner['pid'] or uid != os.getuid()
                or start_time(pid) != owner['start_time']):
            raise RuntimeError('The G HUB control endpoint has a different owner')
        client.sendall(json.dumps({'mode':mode, 'display':os.environ.get('DISPLAY')}).encode())
        reply = json.loads(client.recv(8192))
    if not reply.get('ok'):
        raise RuntimeError(reply.get('error', 'G HUB did not accept the reopen request'))
    return reply


def reopen_or_lock(lock, root, config_path, state_path, mode):
    """Return True after forwarding; False with the cold-start lock held."""
    deadline = time.monotonic() + 60
    recovering = False
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            pass
        owner = session_owner(config_path, state_path)
        if owner and not recovering:
            env = Path(f'/proc/{owner["pid"]}/environ').read_bytes().split(b'\0')
            display = next((os.fsdecode(e.split(b'=', 1)[1]) for e in env if e.startswith(b'DISPLAY=')), '')
            if display != os.environ.get('DISPLAY'):
                raise RuntimeError(f'G HUB is running on display {display}, not this display. '
                                   'Use its Desktop Quit action before starting it here.')
            if owner.get('control_protocol') == 1:
                reply = control_request(root, owner, mode)
                print('G HUB reopen:', reply, flush=True)
                return True
            # Upgrade an old supervisor only when neither frontend nor tray
            # remains. A user click, never background polling, initiates this.
            if any(Path(exe.replace('\\', '/')).name in ('lghub.exe', 'lghub_system_tray.exe')
                   for _, exe in prefix_processes(root / 'prefix')):
                raise RuntimeError('This session uses the older launcher without reopen support. '
                                   'Use the Desktop Quit action once, then launch again.')
            print('Recovering older background-only G HUB session; restarting this prefix.', flush=True)
            stop(config_path, state_path)
            recovering = True
        if time.monotonic() >= deadline:
            raise RuntimeError('G HUB is still starting or stopping. See its session log and try again.')
        time.sleep(.2)


def session(config, root, state_path, performance=False):
    prefix = root / 'prefix'
    if Path(os.environ.get('WINEPREFIX', '')).resolve() != prefix:
        raise RuntimeError('Use the private Wine runner for this installation')
    os.environ.update(config['environment'])
    if config.get('freeze_updates'):
        # The same executable name is used for its installed and temporary copy.
        os.environ['WINEDLLOVERRIDES'] = os.environ.get('WINEDLLOVERRIDES', '') + ';lghub_software_manager.exe=d'
    pid = os.getpid()
    control = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    control.bind(control_address(root))
    control.listen(8)
    control.setblocking(False)
    state = dict(pid=pid, start_time=start_time(pid),
                 boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), control_protocol=1)
    temporary = state_path.with_suffix('.new')
    temporary.write_text(json.dumps(state) + '\n')
    temporary.replace(state_path)
    stopping = False

    def request_stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, request_stop)
    app = prefix / 'drive_c/Program Files/LGHUB'
    monitor = None
    gui_children = []
    starting = True
    last_open = 0

    def open_gui(record, require_recording=False):
        nonlocal monitor, last_open
        gui_children[:] = [child for child in gui_children if child.poll() is None]
        existing = any(Path(exe.replace('\\', '/')).name == 'lghub.exe'
                       for _, exe in prefix_processes(prefix))
        recording = monitor is not None and monitor.poll() is None
        if require_recording and existing and not recording:
            raise RuntimeError('G HUB is already open without performance recording. '
                               'Use Desktop Quit, then Launch with color-picker performance recording.')
        # Coalesce rapid duplicate clicks during process startup. Subsequent
        # launches use G HUB's own second-instance handler to show its window.
        if gui_children and time.monotonic() - last_open < 2:
            return
        gui_args = ['wine', str(app / 'lghub.exe'), '--disable-gpu']
        if record and not existing:
            if recording:
                monitor.terminate()
                monitor.wait(timeout=10)
            with socket.socket() as debugger:
                debugger.bind(('127.0.0.1', 0))
                port = debugger.getsockname()[1]
            gui_args += ['--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}']
        gui_children.append(subprocess.Popen(gui_args, cwd=app))
        last_open = time.monotonic()
        print('Frontend launch requested:', gui_children[-1].pid, 'recording:', record, flush=True)
        if record and not existing:
            output = root / 'logs' / (time.strftime('%Y%m%d-%H%M%S') + f'-{gui_children[-1].pid}-color-performance.jsonl')
            monitor = subprocess.Popen(['node', str(root / 'runner/color_picker_monitor.cjs'),
                                        str(port), str(prefix), str(output)])
            print('Passive color-picker performance recording:', output, flush=True)

    def service_control():
        nonlocal performance
        try:
            client, _ = control.accept()
        except BlockingIOError:
            return
        with client:
            client.settimeout(1)
            try:
                _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    raise RuntimeError('Different user')
                request = json.loads(client.recv(8192))
                if not isinstance(request, dict):
                    raise RuntimeError('Invalid G HUB control request')
                if request.get('display') != os.environ.get('DISPLAY'):
                    raise RuntimeError('G HUB belongs to another display')
                mode = request.get('mode')
                if mode not in ('app', 'performance', 'status'):
                    raise RuntimeError('Unknown G HUB control request')
                if stopping:
                    raise RuntimeError('G HUB is stopping; launch again after it exits')
                if mode != 'status':
                    record = performance or mode == 'performance'
                    if not starting:
                        open_gui(record, require_recording=mode == 'performance')
                    performance = record
                reply = {'ok':True, 'state':'starting' if starting else 'running', 'recording':performance}
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                reply = {'ok':False, 'error':str(error)}
            try:
                client.sendall(json.dumps(reply).encode())
            except OSError:
                pass  # A closed requester must not terminate the session.

    try:
        register_frontend_protocol()
        agent = subprocess.Popen(['wine', str(app / 'lghub_agent.exe')], cwd=app)
        # The native frontend launches its own tray, which can restart an agent
        # that is still initializing. Verify the backend before opening it, and
        # let that one tray own the lifecycle instead of starting a second tray.
        deadline = time.monotonic() + 90
        while not stopping:
            service_control()
            probe = subprocess.run(['node', str(root / 'runner/agent_snapshot.cjs')],
                                   capture_output=True, text=True, timeout=15)
            if probe.returncode == 0:
                (root / 'logs/startup-snapshot.json').write_text(probe.stdout)
                break
            if agent.poll() is not None or time.monotonic() >= deadline:
                print('Last readiness probe:', probe.returncode, probe.stdout, probe.stderr, flush=True)
                raise RuntimeError('The G HUB agent did not become ready. See this session log.')
            time.sleep(1)
        if stopping:
            return 0
        open_gui(performance)
        starting = False
        while not stopping:
            service_control()
            # Closing the main window must not implicitly kill a running agent
            # or tray. The Desktop Quit action remains available indefinitely.
            if agent.poll() is not None and all(child.poll() is not None for child in gui_children):
                remaining = prefix_processes(prefix)
                if not any('lghub_agent.exe' in p[1] or 'lghub_system_tray.exe' in p[1] for p in remaining):
                    print('Agent/UI exited:', agent.returncode, flush=True)
                    return agent.returncode or 0
            time.sleep(0.25)
        return 0
    finally:
        control.close()
        if monitor and monitor.poll() is None:
            monitor.terminate()
            try:
                monitor.wait(timeout=8)
            except subprocess.TimeoutExpired:
                monitor.kill()
                monitor.wait(timeout=5)
        subprocess.run(['wineserver', '-k'], timeout=20, check=False)
        subprocess.run(['wineserver', '-w'], timeout=20, check=False)
        state_path.unlink(missing_ok=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--performance', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('mode', choices=['app', 'performance', 'stop', 'logs', 'snapshot', 'check', '_session'], nargs='?', default='app')
    args = parser.parse_args()
    config_path = args.config.resolve(strict=True)
    root = config_path.parent
    config = json.loads(config_path.read_text())
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    state = logs / 'session.json'
    if args.mode == 'stop':
        stop(config_path, state)
        return 0
    if args.mode == 'logs':
        return subprocess.call(['xdg-open', str(logs)])
    check(config, root, mounted=args.mode == '_session')
    if args.mode == 'snapshot':
        if not prefix_processes(root / 'prefix'):
            raise RuntimeError('Start this G HUB session before taking a snapshot.')
        output = logs / (time.strftime('%Y%m%d-%H%M%S') + f'-{os.getpid()}-agent.json')
        result = subprocess.run(['node', str(root / 'runner/agent_snapshot.cjs'), str(output)], timeout=15)
        print('Read-only agent snapshot:', output)
        if result.returncode:
            raise RuntimeError(f'Agent snapshot is incomplete. Details: {output}')
        return 0
    if args.mode == 'check':
        print(f'Verified {len(config["sha256"])} pinned files; no GUI launched.')
        return 0
    if args.mode == '_session':
        return session(config, root, state, args.performance)
    if not os.environ.get('DISPLAY'):
        raise RuntimeError('Launch this from your graphical session')
    node_check = subprocess.run(['node', '-e', 'process.exit(typeof WebSocket === "function" ? 0 : 1)'],
                                capture_output=True, timeout=10)
    if node_check.returncode:
        raise RuntimeError('This launcher needs the existing Node 22+ with WebSocket support on PATH.')
    with (logs / 'launch.lock').open('a') as lock:
        if reopen_or_lock(lock, root, config_path, state, args.mode):
            return 0
        if prefix_processes(root / 'prefix'):
            raise RuntimeError('This test prefix already has Wine processes. Use its Quit action first.')
        with socket.socket() as endpoint:
            # Recent probe connections leave TIME_WAIT sockets. A real active
            # listener still rejects this bind; a stopped prior run must not.
            endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                endpoint.bind(('127.0.0.1', 9010))
            except OSError:
                raise RuntimeError('Another G HUB agent owns port 9010. Quit that session before starting this one.')
        if config.get('freeze_updates'):
            freeze_updates(root / 'prefix', logs)
        log_path = logs / (time.strftime('%Y%m%d-%H%M%S') + f'-{os.getpid()}.log')
        runner = root / 'runner/run_private_build.py'
        command = [sys.executable, str(runner)]
        for option, relative in config['builds'].items():
            command += [option, str(root / relative)]
        if config.get('freeze_updates'):
            command += ['--freeze-ghub']
        if config.get('frontend_archive'):
            command += ['--frontend-archive', str(root / config['frontend_archive'])]
        command += ['--prefix', str(root / 'prefix'), sys.executable, str(SCRIPT),
                    '--config', str(config_path), '_session']
        if args.mode == 'performance':
            command += ['--performance']
        with log_path.open('w') as output:
            output.write('Frozen G HUB test installation; manifest ' + str(config_path) + '\n')
            output.flush()
            result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'G HUB test session exited {result.returncode}. Log: {log_path}')
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(error, file=sys.stderr)
        if os.environ.get('DISPLAY') and not os.environ.get('GHUB_LAUNCHER_NO_DIALOG') and 'check' not in sys.argv:
            subprocess.run(['kdialog', '--title', 'G HUB — Tested Wine', '--sorry', str(error)], check=False)
        sys.exit(1)
