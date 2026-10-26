#!/usr/bin/env python3
"""User-operated G HUB launchers; all Wine changes stay in a private namespace.

app / installer: open the existing configured lab installation on this display.
stop: end only the session started by this launcher. logs: open its log folder.
check: verify files without launching a GUI. Automated checks must use Xvfb.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

SCRIPT = Path(__file__).resolve()
LAB = SCRIPT.parents[2] / 'wine-lab/codex-run'
PREFIX = LAB / 'prefix'
APP = PREFIX / 'drive_c/Program Files/LGHUB'
ASSETS = LAB / 'launcher-assets'
LOGS = LAB / 'desktop-logs'
STATE = LOGS / 'session.json'


def message(text):
    print(text, file=sys.stderr, flush=True)
    # The check command never opens anything on the desktop.
    if (os.environ.get('DISPLAY') and sys.argv[1:] != ['check']
            and not os.environ.get('GHUB_LAUNCHER_NO_DIALOG')):
        subprocess.run(['kdialog', '--title', 'G HUB — Patched Wine', '--sorry', text], check=False)


def start_time(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def check():
    files = [ASSETS / 'lghub_setup.exe', APP / 'lghub.exe', APP / 'lghub_agent.exe',
             APP / 'api_information_proxy.dll', APP / 'mfsensorgroup.dll',
             LAB / 'wine-build/dlls/setupapi/x86_64-windows/setupapi.dll',
             LAB / 'wine-build/dlls/ntoskrnl.exe/x86_64-windows/ntoskrnl.exe',
             LAB / 'wine-build/dlls/kernelbase/x86_64-windows/kernelbase.dll']
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise RuntimeError('Missing launcher files:\n' + '\n'.join(missing))


def stop():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    if state['boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        return
    pid = state['pid']
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if (start_time(pid) == state['start_time'] and os.fsencode(SCRIPT) in command
                and b'_session' in command):
            signal.pidfd_send_signal(fd, signal.SIGTERM)
    except (FileNotFoundError, ProcessLookupError):
        pass
    finally:
        os.close(fd)


def session(mode):
    if Path(os.environ.get('WINEPREFIX', '')).resolve() != PREFIX:
        raise RuntimeError('Session must run through the private Wine launcher')
    pid = os.getpid()
    state = dict(pid=pid, start_time=start_time(pid), mode=mode,
                 boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    pending = STATE.with_suffix('.new')
    pending.write_text(json.dumps(state) + '\n')
    pending.replace(STATE)
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, request_stop)
    try:
        if mode == 'app':
            subprocess.Popen(['wine', str(APP / 'lghub_agent.exe')], cwd=APP)
            command = ['wine', str(APP / 'lghub.exe'), '--disable-gpu']
        else:
            command = ['wine', str(ASSETS / 'lghub_setup.exe')]
        gui = subprocess.Popen(command, cwd=APP)
        while not stopping:
            try:
                return gui.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        return 0
    finally:
        # WINEPREFIX is the dedicated lab prefix, never the user's default Wine.
        subprocess.run(['wineserver', '-k'], check=False)
        subprocess.run(['wineserver', '-w'], check=False)
        STATE.unlink(missing_ok=True)


def main():
    os.umask(0o077)
    mode = sys.argv[1] if len(sys.argv) > 1 else 'app'
    if mode not in {'app', 'installer', 'stop', 'logs', 'check', '_session'}:
        raise RuntimeError('Expected app, installer, stop, logs, or check')
    if mode == 'check':
        check()
        print('Launcher files verified; no GUI launched.')
        return 0
    LOGS.mkdir(exist_ok=True)
    if mode == 'stop':
        stop()
        return 0
    if mode == 'logs':
        return subprocess.call(['xdg-open', str(LOGS)])
    if mode == '_session':
        return session(sys.argv[2])
    check()
    if not os.environ.get('DISPLAY'):
        raise RuntimeError('Launch this from your graphical desktop.')
    with (LOGS / 'launch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            message('G HUB is already running from a launcher. Close it, or right-click either '
                    'Desktop launcher and choose “Quit G HUB session”, then launch again.')
            return 1
        logfile = LOGS / (time.strftime('%Y%m%d-%H%M%S') + f'-{mode}.log')
        command = [sys.executable, str(SCRIPT.with_name('run_private_build.py')),
                   '--build-dir', str(LAB / 'wine-build'), '--prefix', str(PREFIX),
                   sys.executable, str(SCRIPT), '_session', mode]
        env = dict(os.environ, WINEDEBUG='-all,err+all')
        with logfile.open('w') as output:
            output.write(f'{mode}: existing lab prefix {PREFIX}\n')
            output.flush()
            result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            message(f'G HUB {mode} exited with code {result.returncode}.\nLog: {logfile}')
        return result.returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        message(str(error))
        sys.exit(1)
