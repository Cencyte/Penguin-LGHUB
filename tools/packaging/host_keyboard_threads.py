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
import shlex
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


def analyze_pending_portal(sender, receiver, monitor):
    """Measure consent requests without approving any input in the private session."""
    attempted = analyze(sender, receiver)
    submitted = all(len(case.get('requests', [])) == count
                    and all(event['sent'] == 1 for event in case['requests'])
                    for case, count in zip(attempted['cases'].values(), COUNTS))
    def calls(interface, method):
        return sum(line.startswith('method call ') and
                   f'interface={interface}; member={method}' in line
                   for line in monitor.splitlines())
    frontend = 'org.freedesktop.portal.RemoteDesktop'
    backend = 'org.freedesktop.impl.portal.RemoteDesktop'
    counts = {'create_session': calls(frontend, 'CreateSession'),
              'start': calls(frontend, 'Start'),
              'backend_start': calls(backend, 'Start'),
              'connect_to_eis': calls(frontend, 'ConnectToEIS')}
    received = sum(line.startswith('TIMED_KEY ') for line in receiver.splitlines())
    return {'passed': submitted and counts['start'] > 0 and counts['backend_start'] > 0
            and counts['connect_to_eis'] == 0 and received == 0,
            'scope': 'Private portal requests while consent is pending; no approval performed',
            'submitted_all_cases': submitted, 'portal_calls': counts,
            'received_key_events': received}


def inside():
    if (os.environ.get('PENGUIN_PRIVATE_WAYLAND_PROBE') != '1'
            or os.environ.get('XDG_RUNTIME_DIR') != str(PRIVATE/'run')
            or Path('/dev/input').exists() or any(Path('/dev').glob('hidraw*'))
            or Path('/proc/1/comm').read_text().strip() not in ('bwrap', 'dbus-run-sessio')):
        raise RuntimeError('Disposable display and PID namespaces required')
    config = json.loads((PRIVATE/'control.json').read_text())
    result = {'passed': False}
    receiver = None
    services, service_logs = [], []
    wine_env = dict(os.environ, WINEPREFIX=str(PRIVATE/'prefix'), WINE_GHUB_HOST_KEYBOARD='1',
                    GHUB_HOST_INPUT_PRIVATE_TEST='1', WINEDEBUG='-all',
                    WINEDLLOVERRIDES='winemenubuilder.exe=d;winebus.sys=d;wineusb.sys=d;mscoree,mshtml=d')
    try:
        if config['permission'] == 'portal-pending':
            portal_env = dict(os.environ, QT_QPA_PLATFORM='wayland',
                              QT_LOGGING_RULES='xdp*.debug=true')
            subprocess.run(['dbus-update-activation-environment', 'DISPLAY', 'WAYLAND_DISPLAY',
                            'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE', 'QT_QPA_PLATFORM'],
                           env=portal_env, check=True, timeout=10)
            for name, argv in [('portal-monitor', ['dbus-monitor', '--session',
                    "interface='org.freedesktop.impl.portal.RemoteDesktop'",
                    "interface='org.freedesktop.portal.RemoteDesktop'"]),
                    ('portal-kde', ['/usr/lib/xdg-desktop-portal-kde']),
                    ('portal', ['/usr/lib/xdg-desktop-portal', '-v'])]:
                log = (PRIVATE/(name + '.log')).open('w')
                service_logs.append(log)
                services.append(subprocess.Popen(argv, env=portal_env,
                    stdout=log, stderr=subprocess.STDOUT))
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
            sender_text = (PRIVATE/'sender.log').read_text(errors='replace')
            receiver_text = (PRIVATE/'receiver.log').read_text(errors='replace')
            if config['permission'] == 'portal-pending':
                result = analyze_pending_portal(sender_text, receiver_text,
                    (PRIVATE/'portal-monitor.log').read_text(errors='replace'))
            else:
                result = analyze(sender_text, receiver_text)
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
        for service in reversed(services):
            if service.poll() is None:
                service.terminate()
                try:
                    service.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    service.kill(); service.wait()
        for log in service_logs:
            log.close()
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
    permission = {'test-direct': 'XwaylandEisNoPrompt=true',
                  'test-app': 'XwaylandEisNoPromptApps=lghub_agent.exe',
                  'portal-pending': ''}[args.permission]
    (output/'config/kwinrc').write_text('[Xwayland]\n' + permission + '\n')
    (output/'config/kxkbrc').write_text('[Layout]\nUse=true\nLayoutList=us\n')
    write_json(output/'control.json', {'target': args.target, 'permission': args.permission})
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
        'XDG_MENU_PREFIX': 'plasma-',
        'QT_QPA_PLATFORM': 'offscreen', 'KWIN_COMPOSE': 'Q', 'XWAYLAND_NO_GLAMOR': '1',
        '__EGL_VENDOR_LIBRARY_FILENAMES': '/usr/share/glvnd/egl_vendor.d/50_mesa.json',
        'LIBGL_ALWAYS_SOFTWARE': '1', 'PENGUIN_PRIVATE_WAYLAND_PROBE': '1',
        'QT_NO_XDG_DESKTOP_PORTAL': '1', 'LANG': 'C.UTF-8'}
    for key, value in env.items():
        command += ['--setenv', key, value]
    compositor = ['kwin_wayland_wrapper', '--virtual', '--xwayland', '--no-lockscreen',
                  '--no-global-shortcuts', '--no-kactivities', '--exit-with-session', str(PRIVATE/'session')]
    if args.permission == 'portal-pending':
        start = ('#!/bin/sh\nkbuildsycoca6 --noincremental >' + str(PRIVATE/'sycoca.log') +
                 ' 2>&1\npipewire >' + str(PRIVATE/'pipewire.log') +
                 ' 2>&1 &\nexec ' + shlex.join(compositor) + '\n')
        (output/'compositor').write_text(start)
        (output/'compositor').chmod(0o700)
        compositor = [str(PRIVATE/'compositor')]
    command += ['dbus-run-session', '--', *compositor]
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
                      'portal_calls': result.get('portal_calls'),
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
    parser.add_argument('--permission', choices=('test-app', 'test-direct', 'portal-pending'), default='test-app')
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
