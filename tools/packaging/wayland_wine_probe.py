#!/usr/bin/env python3
"""Exercise packaged Wine keyboard output into an isolated native Wayland app.

No Logitech application, physical input, live desktop, package installation or
real permission changes. Prebuilt fixtures allow testing stock hosts without a
compiler. Test-only permission policies are confined to the disposable KWin.
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

import linux_runtime as linux
from wine_runtime import runtime_environment, verify_runtime
from wayland_input_probe import wait_for

PRIVATE = Path('/tmp/penguin-wayland-probe')
CASES = ('bridge-enabled', 'bridge-disabled', 'other-executable')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def wait_json(path, child, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return json.loads(path.read_text())
        if child.poll() is not None:
            raise RuntimeError(f'{path.name}: worker exited {child.returncode}')
        time.sleep(.05)
    raise TimeoutError(f'Waiting for {path.name}')


def expected_keys():
    # Contract for tools/wine/host_keyboard_probe.c, on the private US layout.
    # Qt symbol, XKB code and modifiers; keypad Enter is distinct from Return.
    events = []
    def pair(symbol, scan, modifiers='0'):
        events.extend([[direction, symbol, scan, modifiers, '0'] for direction in ('down', 'up')])
    for symbol, scan in [('5b', '22'), ('5d', '23'), ('30', '13'), ('1000007', '77')]:
        pair(symbol, scan)
    events.append(['down', '1000021', '69', '4000000', '0'])
    pair('41', '26', '4000000')
    events.append(['up', '1000021', '69', '0', '0'])
    for symbol, scan, modifiers in [('1000012', '71', '0'), ('41', '26', '0'),
                                    ('1000004', '24', '0'), ('1000005', '68', '20000000'),
                                    ('100003b', '60', '0')]:
        pair(symbol, scan, modifiers)
    return events


def private_guard(container=False):
    if (os.environ.get('PENGUIN_PRIVATE_WAYLAND_PROBE') != '1'
            or (not container and os.environ.get('XDG_RUNTIME_DIR') != str(PRIVATE / 'run'))
            or not (PRIVATE / 'control.json').exists()
            or any(Path('/dev').glob('hidraw*')) or Path('/dev/input').exists()):
        raise RuntimeError('Private Wayland test namespace required')


def wine_worker():
    private_guard(container=True)
    config = json.loads((PRIVATE / 'control.json').read_text())
    wine, runtime = Path(config['wine']), Path(config['linux'])
    identity = Path('/etc/os-release').read_text()
    build = json.loads(linux.LOCK.read_text())['build_id']
    if f'BUILD_ID="{build}"' not in identity or Path('/dev/ntsync').exists():
        raise RuntimeError('Pinned userspace with server synchronization required')
    env = runtime_environment(wine, PRIVATE / 'prefix', os.environ)
    env.update(LD_LIBRARY_PATH=linux.library_path(runtime, wine),
               WINEDEBUG='-all,+x11drv', WINEDLLOVERRIDES='winemenubuilder.exe=d;winebus.sys=d;wineusb.sys=d;mscoree,mshtml=d',
               GHUB_HOST_INPUT_PRIVATE_TEST='1')
    # This proof deliberately exercises the existing winex11 driver.
    env.pop('WAYLAND_DISPLAY', None)
    loader, server = str(wine / 'usr/bin/wine'), str(wine / 'usr/bin/wineserver')
    try:
        with (PRIVATE / 'wine-linkage.log').open('w') as log:
            subprocess.run(['/usr/bin/ldd', str(wine / 'usr/lib/wine/x86_64-unix/ntdll.so')],
                           env=env, stdout=log, stderr=subprocess.STDOUT, timeout=15)
        with (PRIVATE / 'wine-boot.log').open('w') as log:
            boot_env = dict(env)
            boot_env.pop('DISPLAY', None)
            boot = subprocess.run([loader, 'cmd', '/c', 'echo PENGUIN_WAYLAND_WINE_READY'],
                                  env=boot_env, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        boot_text = (PRIVATE / 'wine-boot.log').read_text(errors='replace')
        if boot.returncode or 'PENGUIN_WAYLAND_WINE_READY' not in boot_text:
            raise RuntimeError('Private Wine prefix initialization failed')
        # Restart only the disposable prefix's server; explorer inherits its
        # display on startup, so initialization's display-less process ends.
        subprocess.run([server, '-k'], env=env, timeout=10, check=True)
        subprocess.run([server, '-w'], env=env, timeout=10, check=True)
        write_json(PRIVATE / 'wine-ready.json', {'linux_build': build, 'server_sync': True,
                                                'display': env.get('DISPLAY')})
        for case in CASES:
            end = time.monotonic() + 45
            while not (PRIVATE / (case + '.go')).exists():
                if time.monotonic() >= end:
                    raise TimeoutError('No private receiver signal')
                time.sleep(.05)
            case_env = dict(env, WINE_GHUB_HOST_KEYBOARD='0' if case == 'bridge-disabled' else '1')
            binary = 'ordinary.exe' if case == 'other-executable' else 'lghub_agent.exe'
            with (PRIVATE / (case + '.wine.log')).open('w') as log:
                sent = subprocess.run([loader, str(PRIVATE / binary)], env=case_env,
                                      stdout=log, stderr=subprocess.STDOUT, timeout=30)
            lines = (PRIVATE / (case + '.wine.log')).read_text(errors='replace').splitlines()
            submissions = [line for line in lines if line.startswith('INPUT ')]
            write_json(PRIVATE / (case + '.sender.json'), {
                'exit': sent.returncode, 'submissions': submissions,
                'submission_passed': sent.returncode == 0 and len(submissions) == 22
                                     and all('sent=1 ' in line for line in submissions)})
    finally:
        subprocess.run([server, '-k'], env=env, timeout=10, check=False)
        subprocess.run([server, '-w'], env=env, timeout=10, check=False)


def inside():
    private_guard()
    if Path('/proc/1/comm').read_text().strip() not in ('bwrap', 'dbus-run-sessio'):
        raise RuntimeError('Private PID namespace required')
    config = json.loads((PRIVATE / 'control.json').read_text())
    runtime, wine = Path(config['linux']), Path(config['wine'])
    result = {'passed': False, 'scope': 'packaged Wine to native Wayland keyboard transport',
              'permission_policy': config['permission'], 'live_desktop_touched': False,
              'physical_devices_tested': False, 'ghub_application_tested': False, 'cases': {}}
    receiver = worker = None
    services, service_logs = [], []
    try:
        if config['permission'] == 'portal':
            # These services belong to the disposable bus and Wayland socket.
            # Explicit startup avoids dependence on a real systemd user session.
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
                log = (PRIVATE / (name + '.log')).open('w')
                service_logs.append(log)
                services.append(subprocess.Popen(argv, env=portal_env, stdout=log, stderr=subprocess.STDOUT))
        command = linux.container_command(runtime, PRIVATE,
            ['/usr/bin/python3', str(Path(__file__).resolve()), '--worker'], (runtime, wine))
        command[command.index('--unshare-pid')] = '--share-pid'
        write_json(PRIVATE / 'wine-container-command.json', command)
        with (PRIVATE / 'container.log').open('w') as log, (PRIVATE / 'receiver.log').open('w') as recv_log:
            worker = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            result['runtime'] = wait_json(PRIVATE / 'wine-ready.json', worker)
            receiver = subprocess.Popen([str(PRIVATE / 'receiver')],
                env=dict(os.environ, QT_QPA_PLATFORM='wayland'), stdout=recv_log, stderr=subprocess.STDOUT)
            wait_for(PRIVATE / 'receiver.log', 'ACTIVE', receiver)
            previous = 0
            for case in CASES:
                (PRIVATE / (case + '.go')).touch()
                sent = wait_json(PRIVATE / (case + '.sender.json'), worker, 40)
                time.sleep(2)
                lines = (PRIVATE / 'receiver.log').read_text(errors='replace').splitlines()
                all_events = [line.split()[1:] for line in lines if line.startswith('KEY ')]
                events, previous = all_events[previous:], len(all_events)
                expected = expected_keys() if case == 'bridge-enabled' and config['permission'] in ('test-direct', 'test-app') else []
                result['cases'][case] = {**sent, 'events': events, 'expected': expected,
                    'passed': sent['submission_passed'] and events == expected}
            worker.wait(timeout=15)
            result['passed'] = worker.returncode == 0 and all(c['passed'] for c in result['cases'].values())
            if config['permission'] == 'portal':
                monitor = (PRIVATE / 'portal-monitor.log').read_text(errors='replace')
                result['portal_start_requested'] = (
                    'interface=org.freedesktop.impl.portal.RemoteDesktop; member=Start' in monitor)
                result['portal_eis_requested'] = 'member=ConnectToEIS' in monitor
                result['passed'] &= result['portal_start_requested'] and not result['portal_eis_requested']
            elif config['permission'] == 'test-pending-app':
                prompts = []
                for path in Path('/proc').glob('[0-9]*/cmdline'):
                    try:
                        argv = path.read_bytes().split(b'\0')
                        if argv and Path(os.fsdecode(argv[0])).name == 'kwin_eis_prompter':
                            prompts.append([os.fsdecode(a) for a in argv if a])
                    except (FileNotFoundError, ProcessLookupError):
                        pass
                result['authorization_prompts'] = prompts
                result['passed'] &= any(p[1:] == ['--client', 'lghub_agent.exe'] for p in prompts)
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        for child in (receiver, worker, *reversed(services)):
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for log in service_logs:
            log.close()
        write_json(PRIVATE / 'result.json', result)
    # KWin's wrapper can restart a session that exits unsuccessfully. The
    # outer controller reads result.json and returns the actual test status.
    return 0


def run(args):
    output, wine, runtime = args.output.resolve(), args.wine.resolve(), args.linux.resolve()
    for source in (wine, runtime, linux.ROOT):
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError('Keep the disposable output separate from runtime and source trees')
    # Reuse the installer's full inventory/hash checks before executing code.
    verify_runtime(wine)
    linux.verify(runtime)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ('home', 'config', 'run'):
        (output / name).mkdir(mode=0o700)
    shutil.copy2(Path(__file__), output / 'harness.py')
    shutil.copy2(args.receiver, output / 'receiver')
    shutil.copy2(args.sender, output / 'lghub_agent.exe')
    shutil.copy2(args.sender, output / 'ordinary.exe')
    for name in ('receiver', 'lghub_agent.exe', 'ordinary.exe'):
        (output / name).chmod(0o700)
    config = {'wine': str(wine), 'linux': str(runtime), 'permission': args.permission}
    write_json(output / 'control.json', config)
    write_json(output / 'provenance.json', {
        'receiver_sha256': digest(args.receiver), 'sender_sha256': digest(args.sender),
        'wine_manifest_sha256': digest(wine / 'runtime.json'),
        'linux_manifest_sha256': digest(runtime / 'linux-runtime.json'),
        'harness_sha256': digest(Path(__file__)),
        'packaged_wine_version': json.loads((wine / 'runtime.json').read_text())['wine_version']})
    if args.permission == 'test-direct':
        # KWin 6.5 has no per-application direct-EIS allowlist. This measures
        # transport only: consent is NOT tested or bypassed on the live host.
        (output / 'config/kwinrc').write_text('[Xwayland]\nXwaylandEisNoPrompt=true\n')
    elif args.permission == 'test-app':
        (output / 'config/kwinrc').write_text('[Xwayland]\nXwaylandEisNoPromptApps=lghub_agent.exe\n')
    (output / 'config/kxkbrc').write_text('[Layout]\nUse=true\nLayoutList=us\n')
    versions = subprocess.run(['pacman', '-Q', 'kwin', 'plasma-workspace', 'xorg-xwayland',
        'libei', 'qt6-base', 'bubblewrap', 'xdg-desktop-portal', 'xdg-desktop-portal-kde'],
        capture_output=True, text=True)
    (output / 'versions.txt').write_text(versions.stdout + versions.stderr)
    (output / 'session').write_text('#!/bin/sh\nexec /usr/bin/python3 ' +
                                   shlex.quote(str(Path(__file__).resolve())) + ' --inside\n')
    (output / 'session').chmod(0o700)
    command = ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/tmp',
        '--perms', '1777', '--dir', '/tmp/.X11-unix', '--bind', str(output), str(PRIVATE),
        '--bind', str(runtime), str(runtime), '--tmpfs', '/run', '--dev', '/dev', '--proc', '/proc',
        '--unshare-all', '--die-with-parent', '--new-session', '--chdir', str(PRIVATE), '--clearenv']
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(PRIVATE / 'home'),
        'XDG_CONFIG_HOME': str(PRIVATE / 'config'), 'XDG_RUNTIME_DIR': str(PRIVATE / 'run'),
        'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE', 'QT_QPA_PLATFORM': 'offscreen',
        'XDG_MENU_PREFIX': 'plasma-', 'KWIN_COMPOSE': 'Q',
        'LIBGL_ALWAYS_SOFTWARE': '1', 'PENGUIN_PRIVATE_WAYLAND_PROBE': '1',
        'QT_NO_XDG_DESKTOP_PORTAL': '1', 'QT_FORCE_STDERR_LOGGING': '1', 'LANG': 'C.UTF-8',
        'QT_LOGGING_RULES': 'kwin_wayland_libeis.debug=true;kwin_core.debug=true;KWIN_UTILS.debug=true'}
    for key, value in env.items():
        command += ['--setenv', key, value]
    compositor = ['kwin_wayland_wrapper', '--virtual', '--xwayland', '--no-lockscreen',
                  '--no-global-shortcuts', '--no-kactivities', '--exit-with-session', str(PRIVATE / 'session')]
    if args.permission == 'portal':
        # KDE's RemoteDesktop portal also requires the compositor's screencast
        # interface. Give this private compositor its own PipeWire instance.
        start = ('#!/bin/sh\nkbuildsycoca6 --noincremental >' + str(PRIVATE / 'sycoca.log') +
                 ' 2>&1\npipewire >' + str(PRIVATE / 'pipewire.log') + ' 2>&1 &\nexec ' + shlex.join(compositor) + '\n')
        (output / 'compositor').write_text(start)
        (output / 'compositor').chmod(0o700)
        compositor = [str(PRIVATE / 'compositor')]
    command += ['dbus-run-session', '--', *compositor]
    write_json(output / 'command.json', command)
    with (output / 'kwin.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    if not (output / 'result.json').exists():
        raise RuntimeError(f'No result; inspect {output}/kwin.log')
    result = json.loads((output / 'result.json').read_text())
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--wine', type=Path)
    parser.add_argument('--linux', type=Path)
    parser.add_argument('--receiver', type=Path)
    parser.add_argument('--sender', type=Path)
    parser.add_argument('--permission', choices=('portal', 'test-direct', 'test-app', 'test-pending-app'), default='portal')
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        wine_worker()
    elif args.inside:
        sys.exit(inside())
    else:
        if not all((args.output, args.wine, args.linux, args.receiver, args.sender)):
            parser.error('--output, --wine, --linux, --receiver and --sender are required')
        sys.exit(run(args))
