#!/usr/bin/env python3
"""Probe XTEST -> KWin EIS -> native Wayland, isolated from the live desktop.

Needs existing KWin, Xwayland/libei, Qt6 development files, X11/Xtst and bwrap.
Installs nothing. Private home/config, bus, network, PID, /run, /tmp and /dev.
This is a protocol feasibility test, not G HUB or hardware qualification.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent


def wait_for(path, text, child, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if text in path.read_text(errors='replace'):
            return
        if child.poll() is not None:
            raise RuntimeError(f'{path.name}: child exited {child.returncode}')
        time.sleep(.05)
    raise TimeoutError(f'{path.name}: missing {text!r}')


def inside():
    # This entry is invoked only by our private KWin, in an owned namespace.
    if (os.environ.get('PENGUIN_PRIVATE_WAYLAND_PROBE') != '1'
            or os.environ.get('XDG_RUNTIME_DIR') != '/tmp/penguin-wayland-probe/run'
            or Path('/proc/1/comm').read_text().strip() not in ('bwrap', 'dbus-run-sessio')):
        raise RuntimeError('Private namespace required')
    root = Path('/tmp/penguin-wayland-probe')
    result = {'passed': False, 'live_desktop_touched': False,
              'scope': 'XTEST to native Wayland; no Wine or physical device'}
    env = dict(os.environ, QT_QPA_PLATFORM='wayland')
    receiver = None
    try:
        with (root/'receiver.log').open('w') as log:
            receiver = subprocess.Popen([str(root/'receiver')], env=env,
                                        stdout=log, stderr=subprocess.STDOUT)
            wait_for(root/'receiver.log', 'ACTIVE', receiver)
            with (root/'sender.log').open('w') as sender_log:
                sent = subprocess.run([str(root/'penguin-xtest-probe')], env=env,
                                      stdout=sender_log, stderr=subprocess.STDOUT, timeout=8)
            time.sleep(2)
            lines = (root/'receiver.log').read_text(errors='replace').splitlines()
            events = [line.split()[1:] for line in lines if line.startswith('KEY ')]
            # Direction, Qt symbol, native XKB code, modifier state, repeat.
            # Checking the final zero modifier also catches a stuck Control.
            expected = [['down', '41', '26', '0', '0'], ['up', '41', '26', '0', '0'],
                        ['down', '1000021', '69', '4000000', '0'],
                        ['down', '41', '26', '4000000', '0'], ['up', '41', '26', '4000000', '0'],
                        ['up', '1000021', '69', '0', '0'],
                        ['down', '100003b', '60', '0', '0'], ['up', '100003b', '60', '0', '0']]
            control = json.loads((root/'control.json').read_text())['control']
            if control == 'approved':
                passed = events == expected
            else:
                # Must see an actual authorization prompt, not silently missing EIS.
                prompter = False
                for path in Path('/proc').glob('[0-9]*/cmdline'):
                    try:
                        prompter |= b'kwin_eis_prompter' in path.read_bytes()
                    except (FileNotFoundError, ProcessLookupError):
                        pass
                result['authorization_prompt_running'] = prompter
                passed = not events and prompter
            result.update(passed=passed and sent.returncode == 0,
                          sender_exit=sent.returncode, events=events,
                          expected=expected if control == 'approved' else [], control=control,
                          display=os.environ.get('DISPLAY'), wayland_display=os.environ.get('WAYLAND_DISPLAY'))
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        if receiver and receiver.poll() is None:
            receiver.terminate()
            receiver.wait(timeout=5)
        (root/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return 0 if result['passed'] else 1


def run(output, control):
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    versions = subprocess.run(['pacman', '-Q', 'kwin', 'plasma-workspace', 'xorg-xwayland',
                               'libei', 'qt6-base', 'bubblewrap'], text=True, capture_output=True)
    (output/'versions.txt').write_text(versions.stdout + versions.stderr)
    for name in ('home', 'config', 'run'):
        (output/name).mkdir(mode=0o700)
    if control == 'approved':
        # Only the disposable test sender, only in this private home. Never
        # modify the real user's allowlist or disable prompting globally.
        (output/'config/kwinrc').write_text('[Xwayland]\nXwaylandEisNoPromptApps=penguin-xtest-probe\n')
    (output/'control.json').write_text(json.dumps({'control': control})+'\n')
    for compiler, source, binary, packages in (
        ('g++', 'wayland_input_receiver.cpp', 'receiver', ['Qt6Widgets']),
        ('gcc', 'wayland_xtest_sender.c', 'penguin-xtest-probe', ['x11', 'xtst'])):
        flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', *packages], text=True))
        subprocess.run([compiler, '-fPIC', str(HERE/source), '-o', str(output/binary), *flags], check=True)
    session = '#!/bin/sh\nexec /usr/bin/python3 ' + shlex.quote(str(Path(__file__).resolve())) + ' --inside\n'
    (output/'session').write_text(session)
    (output/'session').chmod(0o700)
    args = ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/tmp',
            '--perms', '1777', '--dir', '/tmp/.X11-unix',
            '--bind', str(output), '/tmp/penguin-wayland-probe', '--tmpfs', '/run', '--dev', '/dev', '--proc', '/proc',
            '--unshare-all', '--die-with-parent', '--new-session', '--chdir', '/tmp/penguin-wayland-probe', '--clearenv']
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp/penguin-wayland-probe/home', 'XDG_CONFIG_HOME': '/tmp/penguin-wayland-probe/config',
           'XDG_RUNTIME_DIR': '/tmp/penguin-wayland-probe/run', 'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE',
           'QT_QPA_PLATFORM': 'offscreen', 'KWIN_COMPOSE': 'Q', 'LIBGL_ALWAYS_SOFTWARE': '1',
           'PENGUIN_PRIVATE_WAYLAND_PROBE': '1', 'QT_NO_XDG_DESKTOP_PORTAL': '1',
           'QT_FORCE_STDERR_LOGGING': '1', 'LANG': 'C.UTF-8',
           'QT_LOGGING_RULES': 'kwin_wayland_libeis.debug=true'}
    for key, value in env.items():
        args += ['--setenv', key, value]
    args += ['dbus-run-session', '--', 'kwin_wayland_wrapper', '--virtual', '--xwayland',
             '--no-lockscreen', '--no-global-shortcuts',
             '--no-kactivities', '--exit-with-session', '/tmp/penguin-wayland-probe/session']
    (output/'command.json').write_text(json.dumps(args, indent=2)+'\n')
    with (output/'kwin.log').open('w') as log:
        try:
            proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=45)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'Isolated KWin timed out; inspect {output}/kwin.log')
    if not (output/'result.json').exists():
        raise RuntimeError(f'No probe result (KWin exit {proc.returncode}); inspect {output}/kwin.log')
    result = json.loads((output/'result.json').read_text())
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--control', choices=('approved', 'unapproved'), default='approved')
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.inside and not args.output:
        parser.error('--output is required')
    sys.exit(inside() if args.inside else run(args.output, args.control))
