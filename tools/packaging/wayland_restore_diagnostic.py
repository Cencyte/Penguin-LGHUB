#!/usr/bin/env python3
"""Compare restored G HUB window pixels on disposable KDE/Xwayland.

Uses the production launcher/container/worker. Completes onboarding in a disposable
prefix, then restarts the frontend without debugging before twelve restore cycles.
Captures Xwayland window pixels, not the live desktop or physical scanout. The
virtual KWin backend uses QPainter. Never promotes the test prefix.
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
import threading

import desktop_runtime as desktop
from restore_capture import capture_request, reserve_panel, windows_state, wm_transition
from restore_timing import sample_shutdown
from run_dcomp_tests import Capture
from windows_media_acquire import ROOT, digest_file
from tray_registration import PrivateTray
from probe_clean_agent import runtime_images

SCRIPT = Path(__file__).resolve()
PRIVATE = Path('/tmp/penguin-wayland-launcher-probe')


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def guard():
    if (socket.if_nameindex() != [(1, 'lo')] or Path('/dev/input').exists()
            or list(Path('/dev').glob('hidraw*')) or not (PRIVATE / 'control.json').is_file()):
        raise RuntimeError('Private Wayland test namespace required')


def instrumented_worker(config_path):
    guard()
    original = subprocess.Popen
    probe_started = False
    if (PRIVATE / 'desktop-init-probe.exe').exists():
        original_register = desktop.desktop.register_frontend_protocol
        def register_with_probe():
            original_register()
            results = {}
            for enabled in ('0', '1'):
                env = dict(os.environ, WINE_GHUB_DESKTOP_INIT_TIMEOUT=enabled, PENGUIN_ISOLATED_UI_PROBE='1')
                probe = subprocess.run(['wine', str(PRIVATE / 'desktop-init-probe.exe')],
                    env=env, capture_output=True, text=True, timeout=35)
                results[enabled] = {'exit': probe.returncode, 'output': probe.stdout, 'error': probe.stderr}
            save(config_path.parent / 'desktop-init-probe.json', results)
        desktop.desktop.register_frontend_protocol = register_with_probe

    def window_requests(env):
        request = config_path.parent / 'window-request.json'
        done = None
        while True:
            try:
                value = json.loads(request.read_text())
                if value != done:
                    if value['action'] not in ('minimize', 'query', 'close', 'native-tray-quit'):
                        raise ValueError('Unsupported private window probe action')
                    argv = ['wine', str(PRIVATE / 'window-probe.exe'), value['action']]
                    if value['action'] == 'native-tray-quit':
                        argv = ['wine', str(config_path.parent / 'prefix' / desktop.APP /
                                            'system_tray/lghub_system_tray.exe'), '--killall']
                        print('Private diagnostic: requesting native tray orderly quit', flush=True)
                    with original(argv,
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as child:
                        try:
                            output = child.communicate(timeout=15)[0].decode(errors='replace')
                        except subprocess.TimeoutExpired:
                            child.kill()
                            output = child.communicate()[0].decode(errors='replace') + '\nProbe timed out'
                        save(config_path.parent / 'window-response.json',
                             {'request': value, 'exit': child.returncode, 'output': output})
                    done = value
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(.1)

    def start(argv, *args, **kwargs):
        nonlocal probe_started
        if (isinstance(argv, list) and len(argv) > 1 and argv[0] == 'wine'
                and Path(argv[1]).name == 'lghub.exe' and '--killall' not in argv
                and json.loads((PRIVATE / 'control.json').read_text()).get('startup_timeout_test')
                and not (PRIVATE / 'startup-timeout-injected').exists()):
            (PRIVATE / 'startup-timeout-injected').write_text('Withhold first frontend to exercise timeout and retry\n')
            # No Wine child is created, so the owned agent can still cleanly
            # exit. The supervisor must detect the missing window itself.
            print('Private diagnostic: withholding first frontend startup', flush=True)
            return original(['/usr/bin/true'], *args, **kwargs)
        # Onboarding is confined to the disposable prefix and private display.
        if isinstance(argv, list) and len(argv) > 1 and argv[0] == 'wine' and Path(argv[1]).name == 'lghub.exe' and not (PRIVATE / 'no-debug').exists():
            argv = [*argv, '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=9224']
        if isinstance(argv, list) and len(argv) > 1 and argv[0] == 'wine' and Path(argv[1]).name == 'lghub.exe':
            control = json.loads((PRIVATE / 'control.json').read_text())
            if control.get('trace_window_state'):
                kwargs['env'] = dict(kwargs.get('env', os.environ), WINEDEBUG='-all,+timestamp,+x11drv,+event')
            if control.get('without_workarea_fit'):
                kwargs['env'] = dict(kwargs.get('env', os.environ), PENGUIN_GHUB_FIT_WORKAREA='0')
            if (PRIVATE / 'window-probe.exe').exists() and not probe_started:
                probe_started = True
                threading.Thread(target=window_requests, args=(dict(kwargs.get('env', os.environ),
                    PENGUIN_ISOLATED_UI_PROBE='1'),), daemon=True).start()
            if not json.loads((PRIVATE / 'control.json').read_text())['restore_repaint']:
                kwargs['env'] = dict(kwargs.get('env', os.environ), PENGUIN_GHUB_REPAINT_ON_RESTORE='0')
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
        info = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.kde.KWin', '--object-path', '/KWin', '--method', 'org.kde.KWin.supportInformation'], capture_output=True, text=True)
        (PRIVATE / 'compositor-info.txt').write_text(info.stdout + info.stderr)
        tray = PrivateTray(os.environ['DISPLAY'])
        if control.get('panel_height'):
            reserve_panel(tray, control['height'], control['panel_height'])
        node = Path(config['test_tools']) / 'node/node-v22.21.0-linux-x64/bin/node'
        for index in (1,):
            output = PRIVATE / f'cycle-{index}'; output.mkdir()
            launcher = observer = None
            cycle = {'passed': False}
            try:
                desktop.verify(config, install)
                tray.docked.clear()
                with (output / 'launcher.log').open('w') as log, (output / 'observer.log').open('w') as obs:
                    launcher = subprocess.Popen(['/usr/bin/python3', str(SCRIPT), '--launch',
                        '--config', str(config_path)], stdout=log, stderr=subprocess.STDOUT)
                    observer = subprocess.Popen([str(node), str(ROOT / 'tools/packaging/restore_observer.cjs'), str(output)],
                        stdout=obs, stderr=subprocess.STDOUT)
                    deadline = time.monotonic() + 300
                    snapshots = {}
                    while observer.poll() is None and launcher.poll() is None and time.monotonic() < deadline:
                        tray.poll(); capture_request(output, snapshots, install); time.sleep(.1)
                    cycle['observer_exit'] = observer.wait(timeout=1)
                    deadline = time.monotonic() + 20
                    while not tray.poll() and launcher.poll() is None and time.monotonic() < deadline:
                        time.sleep(.1)
                    cycle['ui'] = json.loads((output / 'ui-verification.json').read_text())
                    cycle['restore_snapshots'] = snapshots
                    if control.get('restore_timing'):
                        timings = [v['timing'] for k, v in snapshots.items()
                                   if k.startswith('restored-') and 'timing' in v]
                        clean = sum(t['visible_incomplete_samples'] == 0 and
                                    t['first_presented_complete_ms'] is not None for t in timings)
                        cycle['presentation'] = {'clean_cycles': clean, 'measured_cycles': len(timings),
                                                 'passed': len(timings) == 12 and clean == 12,
                                                 'scope': 'Sampled X drawable content and requested opacity; not physical scanout'}
                    cycle['tray_dock_requests'] = len(tray.docked)
                    cycle['runtime_images'] = runtime_images(Path(config['wine']))
                    if (install / 'desktop-init-probe.json').exists():
                        cycle['desktop_init'] = json.loads((install / 'desktop-init-probe.json').read_text())
                    cycle['passed'] = cycle['observer_exit'] == 0 and cycle['ui']['passed'] and bool(tray.docked) and len([v for k,v in snapshots.items() if k.startswith('restored-') and v.get('passed')]) == 12
                    if control.get('window_close'):
                        windows_state(install, 'close', 'ordinary-window-close')
                        time.sleep(1)
                        processes = desktop.desktop.prefix_processes(install / 'prefix')
                        cycle['window_close'] = {
                            'supervisor_alive': launcher.poll() is None,
                            'agent_alive': any('lghub_agent.exe' in exe for _, exe in processes),
                            'tray_alive': any('lghub_system_tray.exe' in exe for _, exe in processes)}
                        owner = desktop.desktop.session_owner(config_path, install / 'session.json')
                        desktop.desktop.control_request(install, owner, 'app')
                        deadline = time.monotonic() + 20
                        while True:
                            try:
                                state = windows_state(install, 'query', 'reopened-after-close-' + str(time.monotonic_ns()))
                                if state['visible'] == 1 and state['iconic'] == 0:
                                    break
                            except RuntimeError:
                                if time.monotonic() >= deadline:
                                    raise
                            if time.monotonic() >= deadline:
                                raise RuntimeError('Closed window did not reopen visibly')
                            time.sleep(.25)
                        cycle['window_close']['reopened'] = state['visible'] == 1 and state['iconic'] == 0
                        (output / 'capture-request').write_text('reopened-after-close')
                        capture_request(output, snapshots, install)
                        cycle['window_close']['reopened_pixels'] = snapshots['reopened-after-close']['passed']
                        cycle['passed'] = cycle['passed'] and all(cycle['window_close'].values())
                    if control.get('native_tray_quit'):
                        save(install / 'window-request.json', {'action': 'native-tray-quit', 'label': 'shutdown'})
                        cycle['native_tray_quit_exit'] = launcher.wait(timeout=25)
                        cycle['passed'] = cycle['passed'] and cycle['native_tray_quit_exit'] == 0
                    if control.get('shutdown_timing'):
                        capture = Capture(os.environ['DISPLAY'])
                        try:
                            xid = wm_transition(capture, 'query')
                            cycle['shutdown_presentation'] = sample_shutdown(capture, xid,
                                lambda: desktop.desktop.stop(config_path, install / 'session.json'), guard)
                        finally:
                            capture.close()
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
        result['passed'] = len(result['cycles']) == 1 and all(c['passed'] for c in result['cycles'])
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
    if args.staged_overlay:
        save(output / 'overlay-stage.json', desktop.stage_frontend(roots[0], install / 'desktop.json'))
    if args.window_probe:
        import shutil
        shutil.copyfile(args.window_probe, output / 'window-probe.exe')
    if args.x11_module:
        save(output / 'x11-stage.json', desktop.wine_x11_restore.stage(install / 'desktop.json', args.x11_module))
    if args.desktop_init_module:
        save(output / 'desktop-init-stage.json', desktop.wine_desktop_init.stage(install / 'desktop.json', args.desktop_init_module))
    if args.desktop_init_probe:
        import shutil
        shutil.copyfile(args.desktop_init_probe, output / 'desktop-init-probe.exe')
    save(output / 'control.json', {'installation': str(install), 'restore_repaint': not args.without_repaint,
                                 'reopen': args.reopen, 'windows_minimize': bool(args.window_probe),
                                 'compositor_capture': args.compositor_capture, 'render_node': str(args.render_node or ''),
                                 'trace_window_state': args.trace_window_state, 'height': args.height,
                                 'restore_timing': args.restore_timing,
                                 'native_tray_quit': args.native_tray_quit,
                                 'window_close': args.window_close,
                                 'shutdown_timing': args.shutdown_timing,
                                 'startup_timeout_test': args.startup_timeout_test,
                                 'panel_height': args.panel_height, 'without_workarea_fit': args.without_workarea_fit})
    control = json.loads((output / 'control.json').read_text())
    if args.x11_module:
        control['x11_module_sha256'] = digest_file(args.x11_module)['sha256']
        save(output / 'control.json', control)
    save(output / 'source-sha256.json', {str(p.relative_to(ROOT)): digest_file(p)['sha256'] for p in
        (SCRIPT, desktop.SCRIPT, ROOT / 'tools/packaging/restore_observer.cjs',
         ROOT / 'tools/packaging/restore_capture.py', ROOT / 'tools/packaging/restore_timing.py',
         ROOT / 'tools/wine/tested_desktop_launch.py', ROOT / 'tools/wine/prepare_frontend_startup.py',
         ROOT / 'tools/packaging/wine_desktop_init.py', ROOT / 'tools/packaging/startup_feedback.py')})
    (output / 'session').write_text('#!/bin/sh\nexec python3 ' + shlex.quote(str(SCRIPT)) + ' --inside\n')
    (output / 'session').chmod(0o700)
    compositor = ['kwin_wayland_wrapper', '--virtual', '--width', '1280', '--height', str(args.height), '--xwayland', '--no-lockscreen',
        '--no-global-shortcuts', '--no-kactivities', '--exit-with-session', str(PRIVATE / 'session')]
    (output / 'compositor').write_text('#!/bin/sh\nkbuildsycoca6 --noincremental >' + str(PRIVATE / 'sycoca.log') +
        ' 2>&1\npipewire >' + str(PRIVATE / 'pipewire.log') + ' 2>&1 &\nexec ' + shlex.join(compositor) + '\n')
    (output / 'compositor').chmod(0o700)
    command = ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/tmp', '--perms', '1777', '--dir', '/tmp/.X11-unix',
        '--bind', str(output), str(PRIVATE), '--bind', str(output), str(output),
        '--bind', str(roots[1]), str(roots[1]), '--tmpfs', '/run', '--dev', '/dev', '--proc', '/proc',
        '--unshare-all', '--die-with-parent', '--new-session', '--chdir', str(PRIVATE), '--clearenv']
    if args.render_node:
        node = args.render_node.resolve(strict=True)
        if node.parent != Path('/dev/dri') or not node.name.startswith('renderD') or not node.is_char_device():
            raise ValueError('Only a DRM render node may enter the private compositor')
        command += ['--dev-bind', str(node), str(node)]
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(PRIVATE / 'home'),
        'USER': os.environ.get('USER', str(os.getuid())), 'LOGNAME': os.environ.get('USER', str(os.getuid())),
        'XDG_CONFIG_HOME': str(PRIVATE / 'config'), 'XDG_RUNTIME_DIR': str(PRIVATE / 'run'),
        'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE', 'QT_QPA_PLATFORM': 'offscreen',
        'XDG_MENU_PREFIX': 'plasma-', 'KWIN_COMPOSE': 'O2' if args.render_node else 'Q', 'LIBGL_ALWAYS_SOFTWARE': '1',
        'PENGUIN_ISOLATED_UI_PROBE': '1', 'GHUB_LAUNCHER_NO_DIALOG': '1',
        'QT_NO_XDG_DESKTOP_PORTAL': '1', 'LANG': 'C.UTF-8'}
    if args.compositor_capture:
        # Only the owned compositor inside the isolated bus gets this setting.
        env['KWIN_SCREENSHOT_NO_PERMISSION_CHECKS'] = '1'
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
    parser.add_argument('--without-repaint', action='store_true')
    parser.add_argument('--staged-overlay', action='store_true')
    parser.add_argument('--reopen', action='store_true')
    parser.add_argument('--window-probe', type=Path, help='Private Windows minimize helper, built from restore_window_probe.c')
    parser.add_argument('--render-node', type=Path, help='Use a render node for private OpenGL composition; no DRM card/input/scanout')
    parser.add_argument('--compositor-capture', action='store_true', help='Compare private KWin composited pixels as well as X window pixels')
    parser.add_argument('--height', type=int, default=800)
    parser.add_argument('--restore-timing', action='store_true', help='Sample the first 750 ms of each private restore')
    parser.add_argument('--native-tray-quit', action='store_true', help='Exercise the vendor tray --killall path after restoring')
    parser.add_argument('--window-close', action='store_true', help='Close/reopen the private window while preserving its agent and tray')
    parser.add_argument('--shutdown-timing', action='store_true')
    parser.add_argument('--startup-timeout-test', action='store_true')
    parser.add_argument('--trace-window-state', action='store_true')
    parser.add_argument('--panel-height', type=int, default=0)
    parser.add_argument('--without-workarea-fit', action='store_true')
    parser.add_argument('--x11-module', type=Path, help='Private read-only test overlay, without modifying the baseline runtime')
    parser.add_argument('--desktop-init-module', type=Path)
    parser.add_argument('--desktop-init-probe', type=Path)
    parser.add_argument('identity_marker', nargs='?', choices=('_session',))
    args = parser.parse_args()
    if (args.native_tray_quit or args.window_close) and not args.window_probe:
        parser.error('Close testing requires --window-probe')
    os.umask(0o077)
    if args.inside: sys.exit(inside())
    if args.worker: sys.exit(instrumented_worker(args.config))
    if args.launch: sys.exit(instrumented_launch(args.config))
    if not all((args.candidate, args.linux_runtime, args.wine, args.test_tools, args.output)):
        parser.error('Provide candidate, linux-runtime, wine, test-tools and a new output directory')
    sys.exit(run(args))
