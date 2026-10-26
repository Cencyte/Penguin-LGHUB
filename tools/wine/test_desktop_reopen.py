#!/usr/bin/env python3
"""Reopen regressions, called only by the isolated native update-freeze test."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import hashlib
import re

from tested_desktop_launch import prefix_processes, start_time


def wait_for(predicate, description, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.3)
    raise RuntimeError('Timed out: ' + description)


def main_frontends(prefix):
    found = []
    for pid, exe in prefix_processes(prefix):
        if Path(exe.replace('\\', '/')).name != 'lghub.exe':
            continue
        try:
            argv = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        except OSError:
            continue
        if not any(arg.startswith(b'--type') for arg in argv):
            found.append(pid)
    return found


def exercise_reopen(root, command, env, output, checks, performance=False):
    """The caller owns Xvfb and a disposable prefix in a private network ns."""
    prefix = root / 'prefix'
    host_net = subprocess.check_output(['sudo', '-n', 'readlink', '/proc/1/ns/net'], text=True).strip()
    assert os.readlink('/proc/self/ns/net') != host_net
    assert (prefix / '.overlay-trace-disposable').is_file()
    state_path = root / 'logs/session.json'
    original = json.loads(state_path.read_text())

    def click(mode='app', display=None):
        result = subprocess.run(command + [mode], env=env if display is None else dict(env, DISPLAY=display),
                                capture_output=True, text=True, timeout=35)
        with (output / 'reopen-requests.jsonl').open('a') as log:
            log.write(json.dumps({'mode':mode, 'display':display, 'exit':result.returncode,
                                  'stdout':result.stdout, 'stderr':result.stderr}) + '\n')
        return result

    def has_window():
        tree = subprocess.run(['xwininfo', '-root', '-tree'], env=env,
                              capture_output=True, text=True, check=True).stdout
        (output / 'reopen-windows.txt').write_text(tree)
        match = re.search(r'(0x[0-9a-f]+) "Logitech G HUB": \("lghub.exe"', tree)
        if not match:
            return False
        info = subprocess.run(['xwininfo', '-id', match[1]], env=env,
                              capture_output=True, text=True).stdout
        return 'Map State: IsViewable' in info

    before = wait_for(lambda: main_frontends(prefix), 'first native frontend')
    wait_for(has_window, 'first native G HUB window')
    result = click()
    # The short-lived second instance must exit, leaving the original browser.
    time.sleep(3)
    checks['repeat_click_accepted'] = result.returncode == 0
    checks['repeat_click_reuses_frontend'] = wait_for(lambda: main_frontends(prefix) == before,
                                                     'second instance handoff', timeout=20)
    checks['repeat_click_same_supervisor'] = json.loads(state_path.read_text()) == original
    wrong = click(display=':65530')
    checks['other_display_rejected'] = wrong.returncode != 0 and 'display' in wrong.stderr
    checks['other_display_preserves_frontend'] = main_frontends(prefix) == before
    recording = click('performance')
    if performance:
        checks['recording_repeat_click_accepted'] = recording.returncode == 0
        # ACK means the Wine launch was submitted. Allow that process to
        # become lghub.exe before waiting for its single-instance handoff.
        time.sleep(3)
        wait_for(lambda: main_frontends(prefix) == before, 'recording second instance handoff', timeout=20)
    else:
        checks['recording_requires_fresh_normal_ui'] = (recording.returncode != 0 and
                                                       'without performance recording' in recording.stderr)
    checks['recording_request_preserves_frontend'] = main_frontends(prefix) == before

    # Simulate the user's exact inaccessible state, only in this disposable
    # prefix. Capture each PID identity before signalling; never use pkill.
    victims = []
    for pid, exe in prefix_processes(prefix):
        if Path(exe.replace('\\', '/')).name in ('lghub.exe', 'lghub_system_tray.exe'):
            try:
                victims.append((pid, start_time(pid)))
            except OSError:
                pass
    for pid, born in victims:
        try:
            fd = os.pidfd_open(pid)
            try:
                if start_time(pid) == born and any(p[0] == pid for p in prefix_processes(prefix)):
                    signal.pidfd_send_signal(fd, signal.SIGTERM)
            finally:
                os.close(fd)
        except (ProcessLookupError, FileNotFoundError):
            pass
    wait_for(lambda: not any(Path(exe.replace('\\', '/')).name in ('lghub.exe', 'lghub_system_tray.exe')
                             for _, exe in prefix_processes(prefix)), 'UI and tray exit')
    checks['background_agent_survives'] = any('lghub_agent.exe' in exe for _, exe in prefix_processes(prefix))
    result = click()
    after = wait_for(lambda: main_frontends(prefix), 'reopened native frontend')
    checks['background_only_reopen_accepted'] = result.returncode == 0
    checks['background_only_new_frontend'] = set(after).isdisjoint(before)
    checks['background_only_real_window'] = wait_for(has_window, 'reopened G HUB window')
    checks['background_only_same_supervisor'] = json.loads(state_path.read_text()) == original
    if performance:
        def monitor_attached():
            logs = list((root / 'logs').glob('*-color-performance.jsonl'))
            newest = max(logs, key=lambda path: path.stat().st_mtime)
            return 'attached' in newest.read_text()
        checks['recording_reopened_monitor_attached'] = wait_for(monitor_attached, 'reopened passive monitor')


def exercise_legacy_reopen(root, command, env, output, checks, manager):
    """Migrate an actually running old supervisor, then exercise the new one."""
    prefix = root / 'prefix'
    original = json.loads((root / 'logs/session.json').read_text())
    assert 'control_protocol' not in original
    assert (prefix / '.overlay-trace-disposable').is_file()
    # This fixture runs inside the caller's private network ns and Xvfb.
    host_net = subprocess.check_output(['sudo', '-n', 'readlink', '/proc/1/ns/net'], text=True).strip()
    assert os.readlink('/proc/self/ns/net') != host_net
    for pid, exe in prefix_processes(prefix):
        if Path(exe.replace('\\', '/')).name in ('lghub.exe', 'lghub_system_tray.exe'):
            try:
                born = start_time(pid)
                fd = os.pidfd_open(pid)
                try:
                    if start_time(pid) == born:
                        signal.pidfd_send_signal(fd, signal.SIGTERM)
                finally:
                    os.close(fd)
            except (ProcessLookupError, FileNotFoundError):
                pass
    wait_for(lambda: not any(Path(exe.replace('\\', '/')).name in ('lghub.exe', 'lghub_system_tray.exe')
                             for _, exe in prefix_processes(prefix)), 'legacy UI and tray exit')
    checks['legacy_background_agent_survives'] = any('lghub_agent.exe' in exe for _, exe in prefix_processes(prefix))
    path = root / 'runner/tested_desktop_launch.py'
    updated = Path(__file__).with_name('tested_desktop_launch.py').read_bytes()
    path.write_bytes(updated)
    config_path = root / 'launch.json'
    config = json.loads(config_path.read_text())
    config['sha256']['runner/tested_desktop_launch.py'] = hashlib.sha256(updated).hexdigest()
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    with (output / 'legacy-recovery.log').open('w') as log:
        replacement = subprocess.Popen(command + ['app'], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        checks['legacy_old_supervisor_clean_stop'] = manager.wait(timeout=65) == 0
        def new_state():
            try:
                state = json.loads((root / 'logs/session.json').read_text())
                return state if state['pid'] != original['pid'] else None
            except FileNotFoundError:
                return None
        state = wait_for(new_state, 'replacement supervisor')
        checks['legacy_new_supervisor_protocol'] = state.get('control_protocol') == 1
        wait_for(lambda: main_frontends(prefix), 'replacement UI', timeout=100)
        # This also verifies subsequent clicks no longer restart the backend.
        exercise_reopen(root, command, env, output, checks)
    except BaseException:
        subprocess.run(command + ['stop'], env=env, check=False)
        replacement.wait(timeout=55)
        raise
    return replacement

