#!/usr/bin/env python3
"""Measure Wine worker-thread key transitions in a disposable KWin session.

Uses prebuilt host_keyboard_threads.c and wayland_input_receiver.cpp fixtures.
--os-root can use a mounted OS deployment read-only, with its compositor and
libraries but the running kernel. This does not test hardware, live consent,
SELinux policy or G HUB. No live desktop sockets or input devices are exposed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PRIVATE = Path('/tmp/penguin-keyboard-threads')
CASES = ('same-thread', 'cross-thread', 'reverse-thread', 'rapid-taps',
         'modifier-chord', 'worker-exit', 'intentional-hold')
COUNTS = (6, 6, 6, 48, 4, 2, 2)
# VK -> native XKB code on the private US layout.
SCANS = {0xdb: 34, 0xa2: 37, 0x41: 38}


def analyze(sender, receiver, tolerance_ms=150):
    markers, sends, events = {}, [], []
    for line in sender.splitlines():
        parts = line.split()
        if parts[:1] == ['CASE']:
            markers[parts[1]] = float(parts[2])
        elif parts[:1] == ['SEND']:
            sends.append({'time': float(parts[1]), 'thread': int(parts[2]),
                          'vk': int(parts[3], 16), 'flags': int(parts[5], 16),
                          'sent': int(parts[6]), 'cleanup': parts[7] == '1'})
    for line in receiver.splitlines():
        parts = line.split()
        if parts[:1] == ['TIMED_KEY']:
            events.append({'time': float(parts[1]), 'direction': parts[2],
                           'scan': int(parts[4], 16), 'modifiers': int(parts[5], 16),
                           'repeat': parts[6] == '1'})
    result = {}
    for i, name in enumerate(CASES):
        start, end = markers.get(name), markers.get((*CASES, 'end')[i+1])
        if start is None or end is None:
            result[name] = {'passed': False, 'error': 'Missing sender case marker'}
            continue
        requests = [s for s in sends if start <= s['time'] < end and not s['cleanup']]
        observed = [e for e in events if start <= e['time'] < end]
        transitions = [e for e in observed if not e['repeat']]
        expected = [('up' if s['flags'] & 2 else 'down', SCANS.get(s['vk'])) for s in requests]
        edges = [(e['direction'], e['scan']) for e in transitions]
        latency = ([round(e['time'] - s['time'], 3) for s, e in zip(requests, transitions)]
                   if edges == expected else [])
        repeats = [e for e in observed if e['repeat']]
        # Real holds may repeat until release, but never after its deadline.
        repeat_ok = (all(e['time'] <= requests[-1]['time'] + tolerance_ms for e in repeats)
                     if name == 'intentional-hold' and requests else not repeats)
        modifiers_ok = (name != 'modifier-chord' or
                        [e['modifiers'] for e in transitions] == [0x4000000]*3 + [0])
        passed = (len(requests) == COUNTS[i] and all(s['sent'] == 1 for s in requests)
                  and edges == expected and len(latency) == COUNTS[i]
                  and all(-2 <= ms <= tolerance_ms for ms in latency)
                  and repeat_ok and modifiers_ok)
        result[name] = {'passed': passed, 'expected_transitions': COUNTS[i],
                        'received_transitions': len(transitions), 'repeat_events': len(repeats),
                        'delivery_ms': latency, 'requests': requests, 'events': observed}
    return {'passed': all(case['passed'] for case in result.values()),
            'tolerance_ms': tolerance_ms, 'cases': result}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def inside():
    if (os.environ.get('PENGUIN_PRIVATE_WAYLAND_PROBE') != '1'
            or os.environ.get('XDG_RUNTIME_DIR') != str(PRIVATE/'run')
            or Path('/dev/input').exists() or any(Path('/dev').glob('hidraw*'))
            or Path('/proc/1/comm').read_text().strip() not in ('bwrap', 'dbus-run-sessio')):
        raise RuntimeError('Disposable display and PID namespaces required')
    config = json.loads((PRIVATE/'control.json').read_text())
    result = {'passed': False}
    receiver = None
    wine_env = dict(os.environ, WINEPREFIX=str(PRIVATE/'prefix'), WINE_GHUB_HOST_KEYBOARD='1',
                    GHUB_HOST_INPUT_PRIVATE_TEST='1', WINEDEBUG='-all',
                    WINEDLLOVERRIDES='winemenubuilder.exe=d;winebus.sys=d;wineusb.sys=d;mscoree,mshtml=d')
    try:
        with (PRIVATE/'receiver.log').open('w') as log:
            receiver = subprocess.Popen([str(PRIVATE/'receiver')], stdout=log, stderr=subprocess.STDOUT,
                env=dict(os.environ, QT_QPA_PLATFORM=config['target'],
                         PENGUIN_PRIVATE_RECEIVER_PLATFORM=config['target'], PENGUIN_PRIVATE_INPUT_TIMING='1'))
            end = time.monotonic() + 15
            while 'ACTIVE' not in (PRIVATE/'receiver.log').read_text():
                if receiver.poll() is not None or time.monotonic() > end:
                    raise RuntimeError('Private receiver did not activate')
                time.sleep(.05)
            with (PRIVATE/'sender.log').open('w') as sendlog:
                proc = subprocess.run([str(PRIVATE/'wine/usr/bin/wine'), str(PRIVATE/'lghub_agent.exe')],
                    env=wine_env, stdout=sendlog, stderr=subprocess.STDOUT, timeout=60)
            time.sleep(.2)
            result = analyze((PRIVATE/'sender.log').read_text(errors='replace'),
                             (PRIVATE/'receiver.log').read_text(errors='replace'))
            result['sender_exit'] = proc.returncode
            result['passed'] &= proc.returncode == 0
    except Exception as exc:
        result = {'passed': False, 'error': str(exc)}
    finally:
        subprocess.run([str(PRIVATE/'wine/usr/bin/wineserver'), '-k'], env=wine_env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if receiver and receiver.poll() is None:
            receiver.terminate()
            try:
                receiver.wait(timeout=5)
            except subprocess.TimeoutExpired:
                receiver.kill(); receiver.wait()
        write_json(PRIVATE/'result.json', result)
    return 0  # The outer controller checks the receipt; KWin must not restart it.


def run(args):
    output, wine, osroot = args.output.resolve(), args.wine.resolve(), args.os_root.resolve()
    if output.is_relative_to(wine) or wine.is_relative_to(output):
        raise ValueError('Keep the disposable output separate from Wine')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ('run', 'home', 'config', 'wine'):
        (output/name).mkdir(mode=0o700)
    for source, target in [(Path(__file__), 'harness.py'), (args.sender, 'lghub_agent.exe'),
                           (args.receiver, 'receiver')]:
        shutil.copy2(source, output/target)
    permission = ('XwaylandEisNoPrompt=true' if args.permission == 'test-direct'
                  else 'XwaylandEisNoPromptApps=lghub_agent.exe')
    (output/'config/kwinrc').write_text('[Xwayland]\n' + permission + '\n')
    (output/'config/kxkbrc').write_text('[Layout]\nUse=true\nLayoutList=us\n')
    write_json(output/'control.json', {'target': args.target})
    (output/'session').write_text('#!/bin/sh\nexec /usr/bin/python3 ' + str(PRIVATE/'harness.py') + ' --inside\n')
    (output/'session').chmod(0o700)
    command = ['bwrap', '--ro-bind', str(osroot), '/', '--tmpfs', '/tmp', '--perms', '1777',
        '--dir', '/tmp/.X11-unix', '--bind', str(output), str(PRIVATE),
        '--ro-bind', str(wine), str(PRIVATE/'wine'), '--tmpfs', '/run', '--tmpfs', '/var',
        '--dev', '/dev', '--proc', '/proc', '--unshare-all', '--die-with-parent', '--new-session',
        '--chdir', str(PRIVATE), '--clearenv']
    if args.module:
        command += ['--ro-bind', str(args.module.resolve()), str(PRIVATE/'wine/usr/lib/wine/x86_64-unix/winex11.so')]
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(PRIVATE/'home'), 'XDG_CONFIG_HOME': str(PRIVATE/'config'),
        'XDG_RUNTIME_DIR': str(PRIVATE/'run'), 'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE',
        'QT_QPA_PLATFORM': 'offscreen', 'KWIN_COMPOSE': 'Q', 'XWAYLAND_NO_GLAMOR': '1',
        '__EGL_VENDOR_LIBRARY_FILENAMES': '/usr/share/glvnd/egl_vendor.d/50_mesa.json',
        'LIBGL_ALWAYS_SOFTWARE': '1', 'PENGUIN_PRIVATE_WAYLAND_PROBE': '1',
        'QT_NO_XDG_DESKTOP_PORTAL': '1', 'LANG': 'C.UTF-8'}
    for key, value in env.items():
        command += ['--setenv', key, value]
    command += ['dbus-run-session', '--', 'kwin_wayland_wrapper', '--virtual', '--xwayland',
                '--no-lockscreen', '--no-global-shortcuts', '--no-kactivities', '--exit-with-session', str(PRIVATE/'session')]
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(output/'provenance.json', {'scope': __doc__, 'target': args.target,
        'os_release': (osroot/'etc/os-release').read_text(), 'kernel': os.uname().release,
        'permission': args.permission, 'sender_sha256': digest(args.sender),
        'receiver_sha256': digest(args.receiver), 'harness_sha256': digest(Path(__file__)),
        'wine_manifest_sha256': digest(wine/'runtime.json'),
        'driver_sha256': digest(args.module or wine/'usr/lib/wine/x86_64-unix/winex11.so')})
    write_json(output/'command.json', command)
    with (output/'kwin.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=100, check=True)
    result = json.loads((output/'result.json').read_text())
    print(json.dumps({'passed': result['passed'], 'output': str(output),
                      'cases': {k: v['passed'] for k, v in result.get('cases', {}).items()},
                      'error': result.get('error')}, indent=2))
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--wine', type=Path)
    parser.add_argument('--os-root', type=Path, default=Path('/'))
    parser.add_argument('--module', type=Path, help='Read-only winex11.so override in the disposable namespace')
    parser.add_argument('--sender', type=Path)
    parser.add_argument('--receiver', type=Path)
    parser.add_argument('--target', choices=('wayland', 'xcb'), default='wayland')
    parser.add_argument('--permission', choices=('test-app', 'test-direct'), default='test-app')
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        return inside()
    if not all((args.output, args.wine, args.sender, args.receiver)):
        parser.error('--output, --wine, --sender and --receiver are required')
    return run(args)


if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(main())
