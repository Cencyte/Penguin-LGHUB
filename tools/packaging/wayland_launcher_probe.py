#!/usr/bin/env python3
"""Exercise two complete G HUB startups on disposable KDE/Xwayland.

Uses the production launcher/container/worker, adding a localhost renderer
debugging port only inside the isolated test. Never promotes the test prefix.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time

import desktop_runtime as desktop
from windows_media_acquire import ROOT, digest_file
from tray_registration import PrivateTray
from probe_clean_agent import runtime_images

SCRIPT = Path(__file__).resolve()
PRIVATE = Path('/tmp/penguin-wayland-launcher-probe')


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def guard():
    if (socket.if_nameindex() != [(1, 'lo')] or Path('/dev/input').exists()
            or list(Path('/dev').glob('hidraw*')) or not (PRIVATE / 'control.json').is_file()):
        raise RuntimeError('Private Wayland test namespace required')


def instrumented_worker(config_path):
    guard()
    # Test the production synchronization boundary even when the host exposes
    # NTSYNC. A synthetic /dev must not conceal the CachyOS tray regression.
    desktop.verify_server_sync()
    save(config_path.parent / 'worker-boundary.json', {
        'wine_synchronization': 'server',
        'ntsync_mask_present': Path('/dev/ntsync').exists(),
    })
    original = subprocess.Popen

    def start(argv, *args, **kwargs):
        # Observe the same production frontend; never click or change UI state.
        if isinstance(argv, list) and len(argv) > 1 and argv[0] == 'wine' and Path(argv[1]).name == 'lghub.exe':
            argv = [*argv, '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=9224']
        return original(argv, *args, **kwargs)

    subprocess.Popen = start
    return desktop.worker(json.loads(config_path.read_text()), config_path.parent,
                          config_path.parent / 'session.json', experimental_wayland=True)


def instrumented_launch(config_path):
    guard()
    original = desktop.container_command

    def command(config, root, argv):
        wrapped = original(config, root, ['/usr/bin/python3', str(SCRIPT), '--worker',
            '--config', str(config_path), '--launcher-identity', str(desktop.SCRIPT), '_session'])
        wrapped.insert(wrapped.index('--'), f'--filesystem={PRIVATE}:ro')
        return wrapped

    desktop.container_command = command
    return desktop.launch(config_path, json.loads(config_path.read_text()), config_path.parent, True)


def inside():
    guard()
    if os.environ.get('XDG_RUNTIME_DIR') != str(PRIVATE / 'run'):
        raise RuntimeError('Owned compositor socket required')
    control = json.loads((PRIVATE / 'control.json').read_text())
    install = Path(control['installation'])
    config_path = install / 'desktop.json'
    config = json.loads(config_path.read_text())
    result = {'passed': False, 'cycles': [], 'live_desktop_used': False,
              'physical_devices_tested': False, 'permission_granted': False}
    children, logs, tray = [], [], None
    try:
        env = dict(os.environ, QT_QPA_PLATFORM='wayland')
        subprocess.run(['dbus-update-activation-environment', 'DISPLAY', 'WAYLAND_DISPLAY',
                        'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE', 'QT_QPA_PLATFORM'], env=env, check=True)
        for name, argv in [('portal-kde', ['/usr/lib/xdg-desktop-portal-kde']),
                           ('portal', ['/usr/lib/xdg-desktop-portal', '-v'])]:
            log = (PRIVATE / (name + '.log')).open('w'); logs.append(log)
            children.append(subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT))
        time.sleep(3)
        tray = PrivateTray(os.environ['DISPLAY'])
        node = Path(config['test_tools']) / 'node/node-v22.21.0-linux-x64/bin/node'
        for index in (1, 2):
            output = PRIVATE / f'cycle-{index}'; output.mkdir()
            launcher = observer = None
            cycle = {'passed': False}
            try:
                desktop.verify(config, install)
                tray.docked.clear()
                with (output / 'launcher.log').open('w') as log, (output / 'observer.log').open('w') as obs:
                    launcher = subprocess.Popen(['/usr/bin/python3', str(SCRIPT), '--launch',
                        '--config', str(config_path)], stdout=log, stderr=subprocess.STDOUT)
                    observer = subprocess.Popen([str(node), str(ROOT / 'tools/packaging/frontend_probe.cjs'), str(output)],
                        stdout=obs, stderr=subprocess.STDOUT)
                    deadline = time.monotonic() + 160
                    while observer.poll() is None and launcher.poll() is None and time.monotonic() < deadline:
                        tray.poll(); time.sleep(.1)
                    cycle['observer_exit'] = observer.wait(timeout=1)
                    deadline = time.monotonic() + 20
                    while not tray.poll() and launcher.poll() is None and time.monotonic() < deadline:
                        time.sleep(.1)
                    cycle['ui'] = json.loads((output / 'ui-verification.json').read_text())
                    cycle['tray_dock_requests'] = len(tray.docked)
                    cycle['worker_boundary'] = json.loads((install / 'worker-boundary.json').read_text())
                    cycle['runtime_images'] = runtime_images(Path(config['wine']))
                    cycle['passed'] = cycle['observer_exit'] == 0 and cycle['ui']['passed'] and bool(tray.docked)
                    if control.get('host_ntsync'):
                        cycle['passed'] = cycle['passed'] and cycle['worker_boundary']['ntsync_mask_present']
            except Exception as exc:
                cycle['passed'] = False
                cycle['error'] = str(exc)
            finally:
                if launcher:
                    desktop.desktop.stop(config_path, install / 'session.json')
                    try: cycle['launcher_exit'] = launcher.wait(timeout=25)
                    except subprocess.TimeoutExpired:
                        launcher.terminate(); cycle['launcher_exit'] = launcher.wait(timeout=5)
                if observer and observer.poll() is None:
                    observer.terminate(); observer.wait(timeout=5)
                cycle['remaining_prefix_processes'] = desktop.desktop.prefix_processes(install / 'prefix')
                try:
                    desktop.verify(config, install)
                    cycle['installation_intact_after_exit'] = True
                except Exception as exc:
                    cycle['installation_intact_after_exit'] = False
                    cycle['integrity_error'] = str(exc)
                cycle['passed'] = bool(cycle['passed'] and cycle.get('launcher_exit') == 0
                    and not cycle['remaining_prefix_processes'] and cycle['installation_intact_after_exit'])
                result['cycles'].append(cycle)
                save(PRIVATE / 'result.json', result)
            if not cycle['passed']:
                break
        result['passed'] = len(result['cycles']) == 2 and all(c['passed'] for c in result['cycles'])
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        if tray: tray.close()
        for child in reversed(children):
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        for log in logs: log.close()
        save(PRIVATE / 'result.json', result)
    return 0  # The controller reads the receipt; do not make KWin restart a failed test.


def run(args):
    output = args.output.resolve()
    roots = [args.candidate.resolve(), args.linux_runtime.resolve(), args.wine.resolve(), args.test_tools.resolve()]
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (*roots, ROOT)):
        raise ValueError('Keep disposable test output separate from its source inputs')
    output.mkdir(mode=0o700)
    for name in ('home', 'config', 'run'): (output / name).mkdir(mode=0o700)
    install = output / 'installation'
    desktop.prepare(*roots, install)
    if args.x11_restore_module:
        save(output / 'x11-stage.json', desktop.wine_x11_restore.stage(install / 'desktop.json', args.x11_restore_module))
    if args.desktop_init_module:
        save(output / 'desktop-init-stage.json', desktop.wine_desktop_init.stage(install / 'desktop.json', args.desktop_init_module))
    host_ntsync = Path('/dev/ntsync').exists()
    save(output / 'control.json', {'installation': str(install), 'host_ntsync': host_ntsync})
    save(output / 'source-sha256.json', {str(p.relative_to(ROOT)): digest_file(p)['sha256'] for p in
        (SCRIPT, desktop.SCRIPT, ROOT / 'tools/packaging/frontend_probe.cjs',
         ROOT / 'tools/packaging/server_sync.py', ROOT / 'tools/packaging/wine_desktop_init.py',
         ROOT / 'tools/packaging/wine_x11_restore.py', ROOT / 'tools/wine/prepare_frontend_startup.py',
         ROOT / 'tools/wine/tested_desktop_launch.py')})
    (output / 'session').write_text('#!/bin/sh\nexec python3 ' + shlex.quote(str(SCRIPT)) + ' --inside\n')
    (output / 'session').chmod(0o700)
    compositor = ['kwin_wayland_wrapper', '--virtual', '--xwayland', '--no-lockscreen',
        '--no-global-shortcuts', '--no-kactivities', '--exit-with-session', str(PRIVATE / 'session')]
    (output / 'compositor').write_text('#!/bin/sh\nkbuildsycoca6 --noincremental >' + str(PRIVATE / 'sycoca.log') +
        ' 2>&1\npipewire >' + str(PRIVATE / 'pipewire.log') + ' 2>&1 &\nexec ' + shlex.join(compositor) + '\n')
    (output / 'compositor').chmod(0o700)
    command = ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/tmp', '--perms', '1777', '--dir', '/tmp/.X11-unix',
        '--bind', str(output), str(PRIVATE), '--bind', str(output), str(output),
        '--bind', str(roots[1]), str(roots[1]), '--tmpfs', '/run', '--dev', '/dev', '--proc', '/proc',
        '--unshare-all', '--die-with-parent', '--new-session', '--chdir', str(PRIVATE), '--clearenv']
    if host_ntsync:
        command += ['--dev-bind', '/dev/ntsync', '/dev/ntsync']
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(PRIVATE / 'home'),
        'USER': os.environ.get('USER', str(os.getuid())), 'LOGNAME': os.environ.get('USER', str(os.getuid())),
        'XDG_CONFIG_HOME': str(PRIVATE / 'config'), 'XDG_RUNTIME_DIR': str(PRIVATE / 'run'),
        'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE', 'QT_QPA_PLATFORM': 'offscreen',
        'XDG_MENU_PREFIX': 'plasma-', 'KWIN_COMPOSE': 'Q', 'LIBGL_ALWAYS_SOFTWARE': '1',
        'PENGUIN_ISOLATED_UI_PROBE': '1', 'GHUB_LAUNCHER_NO_DIALOG': '1',
        'QT_NO_XDG_DESKTOP_PORTAL': '1', 'LANG': 'C.UTF-8'}
    for key, value in env.items(): command += ['--setenv', key, value]
    command += ['dbus-run-session', '--', str(PRIVATE / 'compositor')]
    save(output / 'command.json', command)
    with (output / 'kwin.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=420)
    result = json.loads((output / 'result.json').read_text())
    print(json.dumps(result, indent=2))
    return int(not result['passed'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('candidate', 'linux-runtime', 'wine', 'test-tools', 'output', 'config', 'launcher-identity'):
        parser.add_argument('--' + key, type=Path)
    for key in ('inside', 'worker', 'launch'): parser.add_argument('--' + key, action='store_true')
    parser.add_argument('--x11-restore-module', type=Path)
    parser.add_argument('--desktop-init-module', type=Path)
    parser.add_argument('identity_marker', nargs='?', choices=('_session',))
    args = parser.parse_args()
    os.umask(0o077)
    if args.inside: sys.exit(inside())
    if args.worker: sys.exit(instrumented_worker(args.config))
    if args.launch: sys.exit(instrumented_launch(args.config))
    if not all((args.candidate, args.linux_runtime, args.wine, args.test_tools, args.output)):
        parser.error('Provide candidate, linux-runtime, wine, test-tools and a new output directory')
    sys.exit(run(args))
