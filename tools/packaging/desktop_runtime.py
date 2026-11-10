#!/usr/bin/env python3
"""User-operated launcher with automatic X11 or KDE/Xwayland session selection.

prepare creates a separate working prefix; check reads runtime/session capabilities.
launch is deliberately interactive. This container provides library portability,
not a security boundary: X11, the host network and permitted devices are shared.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback

import linux_runtime as linux
import linux_probe
import wayland_session
import wine_x11_restore
import wine_desktop_init
import startup_feedback
from installation_state import record_launchers, load as load_installation
from prepare_clean_prefix import APP, BASELINE, DATA, verify_prepared
from windows_media_acquire import ROOT, digest_file, space_check, work_lock
from wine_runtime import runtime_environment
from server_sync import verify_server_sync

sys.path.insert(0, str(ROOT / 'tools/wine'))
import tested_desktop_launch as desktop
from prepare_tested_desktop import desktop_environment
from prepare_frontend_startup import prepare as prepare_frontend

SCRIPT = Path(__file__).resolve()
ICON = ROOT / 'assets/penguin-lghub-logo.png'
# Reuse the tested supervisor's PID/start-time/boot-id validation and its
# authenticated reopen endpoint. --share-pid keeps its host identity valid.
desktop.SCRIPT = SCRIPT


def session_environment(source, experimental_wayland=False):
    env = linux.clean_environment()
    for key in ('DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'XDG_SESSION_TYPE',
                'DBUS_SESSION_BUS_ADDRESS', 'PULSE_SERVER', 'LANG', 'LC_ALL',
                'PENGUIN_GHUB_HOST_PROFILE_DIR', 'PENGUIN_GHUB_FOCUS_TOKEN', 'PENGUIN_GHUB_FOCUS_FILE'):
        if source.get(key):
            env[key] = source[key]
    if experimental_wayland:
        for key in ('WAYLAND_DISPLAY', 'XDG_CURRENT_DESKTOP'):
            if source.get(key):
                env[key] = source[key]
    return env


def session_errors(env, experimental_wayland=False):
    errors = []
    if experimental_wayland:
        if env.get('XDG_SESSION_TYPE') != 'wayland':
            errors.append('This launcher is configured for Plasma (Wayland). Log out and select that session, or use the launcher configured for X11.')
        if 'KDE' not in env.get('XDG_CURRENT_DESKTOP', '').upper().split(':'):
            errors.append('Wayland support currently requires KDE Plasma.')
        if not env.get('WAYLAND_DISPLAY'):
            errors.append('The Wayland display is missing. Launch from your Plasma desktop.')
    elif env.get('XDG_SESSION_TYPE') != 'x11':
        errors.append('This launcher is configured for X11. Select Plasma (X11), or use a Penguin-LGHUB launcher configured for Wayland.')
    if not re.fullmatch(r':\d+(?:\.\d+)?', env.get('DISPLAY', '')):
        errors.append('A local X11/Xwayland display is required. Launch from the desktop, not an SSH-forwarded display.')
    return errors


def resolve_wayland(env, override=None):
    """Resolve each invocation from the login session, never DISPLAY alone."""
    if override is not None:
        return override
    session = env.get('XDG_SESSION_TYPE')
    if session not in ('x11', 'wayland'):
        raise ValueError('Cannot determine the desktop session. Open Penguin-LGHUB from a local X11 or Plasma (Wayland) login.')
    return session == 'wayland'


def session_report(env, experimental_wayland=None):
    try:
        experimental_wayland = resolve_wayland(env, experimental_wayland)
    except ValueError as error:
        return {'mode': 'unknown', 'errors': [str(error)]}
    report = {'mode': 'experimental-kde-xwayland' if experimental_wayland else 'x11',
              'errors': session_errors(env, experimental_wayland)}
    if experimental_wayland and not report['errors']:
        report = wayland_session.inspect(session_environment(env, True))
    return report


def show_message(title, message, error=False):
    # Called only for user-requested actions. No dialog timeout or input capture.
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        return
    if shutil.which('kdialog'):
        command = ['kdialog', '--title', title, '--sorry' if error else '--msgbox', message]
    elif shutil.which('xmessage') and os.environ.get('DISPLAY'):
        command = ['xmessage', '-center', '-title', title, '-buttons', 'OK:0', message]
    else:
        return
    subprocess.run(command, check=False)


def device_status():
    result = []
    for path in sorted(Path('/sys/class/hidraw').glob('hidraw*')):
        try:
            data = dict(line.split('=', 1) for line in (path / 'device/uevent').read_text().splitlines() if '=' in line)
            fields = data.get('HID_ID', '').split(':')
            if len(fields) != 3 or int(fields[1], 16) != 0x046d:
                continue
            node = Path('/dev') / path.name
            result.append({'path': str(node), 'name': data.get('HID_NAME', ''),
                           'vendor': '046d', 'product': f'{int(fields[2], 16):04x}',
                           'readable': os.access(node, os.R_OK), 'writable': os.access(node, os.W_OK)})
        except (OSError, ValueError):
            continue
    return result


def container_command(config, output, command):
    root, wine, test_tools = (Path(config[k]) for k in ('linux_runtime', 'wine', 'test_tools'))
    # pressure-vessel imports graphics/udev and live desktop sockets, including
    # XAUTHORITY. Do not replace its driver search paths with host LD_* values.
    args = linux.container_command(root, output, command, (root, wine, test_tools))
    args[args.index('--graphics-provider=')] = '--graphics-provider=/'
    args[args.index('--unshare-pid')] = '--share-pid'
    # Freeze the installation payload AND updater-owned depot state. Disabling
    # downloads does not disable the updater's local pruning/recovery paths.
    # Per-user settings, registry and ProgramData/LGHUBData remain writable.
    # Apply this *inside* pressure-vessel, after the writable output mount.
    bwrap = root / linux.DEPOT / 'pressure-vessel/libexec/steam-runtime-tools-0/srt-bwrap'
    boundary = args.index('--') + 1
    app = output / 'prefix' / APP
    data = output / 'prefix' / DATA
    args[boundary:boundary] = ['/usr/bin/python3', str(ROOT / 'tools/packaging/server_sync.py'),
                               str(bwrap), '--bind', '/', '/', '--dev-bind', '/dev', '/dev',
                               '--ro-bind', str(app), str(app),
                               '--ro-bind', str(data), str(data), '--']
    if config.get('frontend_override'):
        source = frontend_override(config, output)
        inner_end = args.index('--', boundary)
        args[inner_end:inner_end] = ['--ro-bind', str(source), str(app / 'resources/app.asar')]
    if config.get('x11_restore_override'):
        source, target = wine_x11_restore.overlay(config, output)
        inner_end = args.index('--', boundary)
        args[inner_end:inner_end] = ['--ro-bind', str(source), str(target)]
    if config.get('desktop_init_override'):
        source, target = wine_desktop_init.overlay(config, output)
        inner_end = args.index('--', boundary)
        args[inner_end:inner_end] = ['--ro-bind', str(source), str(target)]
    return list(map(str, args))


def frontend_override(config, root):
    name = config['frontend_override']
    if (not re.fullmatch(r'frontend/[0-9a-f]{64}\.asar', name)
            or name not in config['fixed_files']):
        raise ValueError('Invalid verified frontend overlay')
    target = root / name
    if (target.is_symlink() or not target.resolve().is_relative_to(root.resolve())
            or digest_file(target)['sha256'] != config['fixed_files'][name]):
        raise ValueError('Frontend overlay changed')
    return target


def stage_frontend(candidate, config_path):
    """Stage a verified, read-only frontend overlay for the next cold launch.

    The running prefix and settings remain untouched. Both the old and new
    configs are valid during the atomic config replacement; no live process
    observes a half-written vendor archive.
    """
    root = config_path.parent
    config = json.loads(config_path.read_text())
    verify(config, root)
    verify_prepared(candidate)
    receipt_path = candidate / 'clean-prefix.json'
    if digest_file(receipt_path)['sha256'] != config['candidate_manifest_sha256']:
        raise ValueError('Frontend update requires this installation\'s verified acquisition candidate')
    receipt = json.loads(receipt_path.read_text())
    folder = root / 'frontend'
    folder.mkdir(exist_ok=True)
    if folder.is_symlink():
        raise ValueError('Frontend overlay directory must not be a symlink')
    temporary = folder / f'prepared-{os.getpid()}.asar'
    try:
        report = prepare_frontend(candidate / 'frontend/color-preview.asar', temporary, receipt['color_archive'])
        name = 'frontend/' + report['output_sha256'] + '.asar'
        temporary.replace(root / name)
        config['fixed_files'][name] = report['output_sha256']
        config['frontend_override'] = name
        config['frontend_initialization'] = report
        verify(config, root)
        backup = root / ('desktop-before-' + report['output_sha256'][:12] + '.json')
        if not backup.exists():
            with backup.open('xb') as stream:
                stream.write(config_path.read_bytes())
        staged_config = config_path.with_suffix(f'.{os.getpid()}.new')
        staged_config.write_text(json.dumps(config, indent=2) + '\n')
        staged_config.replace(config_path)
        return {'overlay': name, 'takes_effect': 'next cold launch', 'previous_config': str(backup)}
    finally:
        temporary.unlink(missing_ok=True)


def activate_frontend(candidate, output, receipt, fixed):
    # Activate the verified frontend transformation, not the original vendor
    # archive left in the acquisition candidate. This preserves the established
    # color-picker repair when moving from the development overlay to a bundle.
    frontend = 'prefix/' + APP + '/resources/app.asar'
    target = output / frontend
    temporary = target.with_suffix('.prepared')
    report = prepare_frontend(candidate / 'frontend/color-preview.asar', temporary, receipt['color_archive'])
    temporary.replace(target)
    fixed[frontend] = report['output_sha256']
    return report


def prepare(candidate, linux_root, wine, test_tools, output):
    for p in (candidate, linux_root, wine, test_tools, ROOT):
        if output.is_relative_to(p) or p.is_relative_to(output):
            raise ValueError('Keep the new desktop installation separate from its source inputs')
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError('Use a new desktop directory with an existing parent')
    verify_prepared(candidate)
    linux.verify(linux_root)
    linux_probe.verify(test_tools)
    receipt = json.loads((candidate / 'clean-prefix.json').read_text())
    linux_probe.matching_runtime(receipt, wine)
    budget = json.loads((ROOT / 'packaging/install-storage.json').read_text())
    space_check(output.parent, budget['components']['desktop']['budget_bytes'])
    output.mkdir(mode=0o700)
    subprocess.run(['cp', '-a', '--reflink=auto', str(candidate / 'prefix'), str(output / 'prefix')], check=True)
    for name in ('home', 'logs', 'runner'):
        (output / name).mkdir()
    for name in ('agent_snapshot.cjs', 'color_picker_monitor.cjs'):
        shutil.copy2(ROOT / 'tools/wine' / name, output / 'runner' / name)
    # Wine's user-folder links in a generated prefix may still name the build
    # account. Keep their data in this installation after relocation.
    users = output / 'prefix/drive_c/users'
    for link in users.rglob('*'):
        if link.is_symlink():
            link.unlink()
            link.mkdir()
    fixed = {name: sha for name, sha in receipt['files'].items()
             if name.startswith(('prefix/' + APP + '/', 'prefix/' + DATA + '/',
                                 'prefix/drive_c/windows/system32/'))}
    frontend = activate_frontend(candidate, output, receipt, fixed)
    config = {'schema': 1, 'kind': 'penguin-desktop-runtime', 'linux_runtime': str(linux_root),
              'wine': str(wine), 'test_tools': str(test_tools), 'environment': desktop_environment(receipt['runtime_environment']),
              'packaged_runtime': receipt['packaged_runtime'], 'freeze_updates': True,
              'candidate_manifest_sha256': digest_file(candidate / 'clean-prefix.json')['sha256'],
              'fixed_files': fixed, 'installation_state': 'frozen-depots-v1',
              'frontend_initialization': frontend, 'end_to_end_qualified': False}
    if desktop.native_profiles.available(output / 'prefix' / APP):
        config['native_profiles'] = 1
    if json.loads((wine / 'runtime.json').read_text()).get('source_build'):
        config['integrated_wine_fixes'] = ['desktop-init', 'x11-restore']
    (output / 'desktop.json').write_text(json.dumps(config, indent=2) + '\n')
    desktop.freeze_updates(output / 'prefix', output / 'logs')
    return output / 'desktop.json'


def verify(config, root):
    if config.get('schema') != 1 or config.get('kind') != 'penguin-desktop-runtime':
        raise ValueError('Not a packaged desktop installation')
    for name, sha in config['fixed_files'].items():
        target = root / name
        if (not target.resolve().is_relative_to(root) or not target.is_file()
                or digest_file(target)['sha256'] != sha):
            raise ValueError('Desktop component changed: ' + name)
    if config.get('frontend_override'):
        frontend_override(config, root)
    if config.get('x11_restore_override'):
        wine_x11_restore.overlay(config, root)
    if config.get('desktop_init_override'):
        wine_desktop_init.overlay(config, root)
    verify_installation_inventory(config)
    verify_environment(config)
    linux.verify(Path(config['linux_runtime']))
    linux_probe.verify(Path(config['test_tools']))
    linux_probe.matching_runtime(config, Path(config['wine']))
    if config.get('integrated_wine_fixes'):
        runtime = json.loads((Path(config['wine']) / 'runtime.json').read_text())
        if not runtime.get('source_build') or config['integrated_wine_fixes'] != ['desktop-init', 'x11-restore']:
            raise ValueError('Integrated Wine fixes require the verified source build')


def verify_installation_inventory(config):
    files = config.get('fixed_files', {})
    data = 'prefix/' + DATA + '/'
    if (config.get('installation_state') != 'frozen-depots-v1'
            or any(data + name not in files for name in
                   ('current.json', 'version.json', 'installation.json', 'groups.json', 'keys.json', 'periodic_check.json'))
            or not any(name.startswith(data + 'depots/') for name in files)
            or not any(name.startswith(data + 'cache/') for name in files)):
        raise ValueError('This installation lacks verified frozen depot state. Prepare a new desktop installation from the verified candidate; do not bypass the G HUB missing-files check.')


def verify_environment(config):
    """Check behavior gates as well as DLL hashes; reject retired lab policy."""
    expected = desktop_environment(json.loads(BASELINE.read_text())['environment'])
    actual = config.get('environment', {})
    for key, value in expected.items():
        if key not in ('WINEDEBUG', 'WINEDLLOVERRIDES') and actual.get(key) != value:
            raise ValueError('Required compatibility setting changed: ' + key)
    if set(actual) - set(expected):
        raise ValueError('Unexpected runtime environment settings')
    def overrides(text):
        result = {}
        for entry in text.split(';'):
            names, separator, mode = entry.partition('=')
            if separator:
                result.update((n.strip().lower(), mode.strip().lower()) for n in names.split(','))
        return result
    required = overrides(expected['WINEDLLOVERRIDES'])
    required['lghub_software_manager.exe'] = 'd'
    present = overrides(actual.get('WINEDLLOVERRIDES', ''))
    if present != required or not config.get('freeze_updates'):
        raise ValueError('Desktop DLL/device/update policy differs from the packaged recipe')


def worker(config, root, state, experimental_wayland=False):
    expected = json.loads(linux.LOCK.read_text())['build_id']
    if f'BUILD_ID="{expected}"' not in Path('/etc/os-release').read_text():
        raise RuntimeError('Desktop worker must run inside the pinned Linux userspace')
    verify_server_sync()
    wine, linux_root, test_tools = (Path(config[k]) for k in ('wine', 'linux_runtime', 'test_tools'))
    env = runtime_environment(wine, root / 'prefix', os.environ)
    env.update(config['environment'])
    if config.get('desktop_init_override') or 'desktop-init' in config.get('integrated_wine_fixes', []):
        env['WINE_GHUB_DESKTOP_INIT_TIMEOUT'] = '1'
    env['PATH'] = str(wine / 'usr/bin') + ':' + str(test_tools / 'node/node-v22.21.0-linux-x64/bin') + ':/usr/bin:/bin'
    # Keep pressure-vessel's imported driver paths ahead of our supplementary
    # non-graphics libraries. Never import the caller's arbitrary LD_* values.
    env['LD_LIBRARY_PATH'] = ':'.join(filter(None, (os.environ.get('LD_LIBRARY_PATH'), linux.library_path(linux_root, wine))))
    # Both desktop modes use winex11.drv. Application-initiated minimization
    # can leave Wine iconic after KWin restores the X window, especially when
    # the work area is shorter than Chromium's minimum. These are X11-driver
    # corrections, not Wayland-only features. Keep the bounded repaint and
    # work-area fit paired with the verified restore-state implementation.
    env['PENGUIN_GHUB_REPAINT_ON_RESTORE'] = '1'
    env['PENGUIN_GHUB_FIT_WORKAREA'] = '1'
    if config.get('x11_restore_override') or 'x11-restore' in config.get('integrated_wine_fixes', []):
        env['WINE_GHUB_RESTORE_STATE'] = '1'
    if experimental_wayland:
        # Keep native Wayland for host services; Wine uses authenticated Xwayland.
        env.pop('WAYLAND_DISPLAY', None)
        env['PENGUIN_GHUB_GRACEFUL_CLOSE'] = '1'
    os.environ.clear()
    os.environ.update(env)
    if experimental_wayland:
        with (root / 'logs/display-driver.log').open('a') as log:
            subprocess.run(['wine', 'reg', 'add', r'HKCU\Software\Wine\Drivers',
                            '/v', 'Graphics', '/d', 'x11', '/f'], env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
    configure_fonts(root, env)
    try:
        return desktop.session(config, root, state)
    except desktop.FrontendStartupTimeout as error:
        print(error, flush=True)
        return 75  # Only this pre-window failure permits one cold retry.


def configure_fonts(root, env):
    # XAML treats a missing default family as fatal while measuring menu text.
    # DirectWrite's Replacements key only applies when the requested family is
    # absent. DejaVu Sans is already part of the verified Linux runtime.
    # Also repair installations prepared before this recipe was introduced;
    # this runs before the launcher starts the agent/UI, not against a live app.
    with (root / 'logs/font-configuration.log').open('a') as log:
        subprocess.run(['wine', 'reg', 'add', r'HKCU\Software\Wine\Fonts\Replacements',
                        '/v', 'Segoe UI', '/d', 'DejaVu Sans', '/f'],
                       env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)


def run_container_session(config_path, config, root, command, stream, feedback, experimental_wayland):
    """Observe readiness while supervising; retry only a pre-window timeout."""
    for attempt in range(2):
        with desktop.native_profiles.HostSession(config, root) as profiles:
            env = session_environment(os.environ, experimental_wayland)
            env.update(profiles.environment())
            with subprocess.Popen(container_command(config, root, command), env=env,
                                  stdout=stream, stderr=subprocess.STDOUT) as child:
                while child.poll() is None:
                    profiles.poll()
                    if not feedback.completed:
                        owner = desktop.session_owner(config_path, root / 'session.json')
                        if owner and desktop.frontend_ready(root, owner.get('startup_token')):
                            feedback.complete()
                    time.sleep(.2)
                status = child.returncode
        if status != 75 or attempt:
            return status
        if desktop.prefix_processes(root / 'prefix'):
            raise RuntimeError('Startup timed out and Wine did not stop completely. See the session log.')
        stream.write('Frontend startup timed out; retrying the stopped private session once.\n')
        stream.flush()


def launch(config_path, config, root, experimental_wayland=None):
    # Complete X11 startup feedback when the real window is ready or startup
    # fails. Plasma manages its separate Wayland activation feedback itself.
    with startup_feedback.Feedback(os.environ) as feedback:
        return launch_with_feedback(config_path, config, root, feedback, experimental_wayland)


def launch_with_feedback(config_path, config, root, feedback, experimental_wayland=None):
    receipt = root.parent / 'installation.json'
    if receipt.exists() and not load_installation(root.parent).get('installed'):
        raise RuntimeError('Installation has not committed successfully; rerun the installer to recover.')
    if (root.parent / '.uninstalling').exists():
        raise RuntimeError('This installation is being removed; run --uninstall again if interrupted.')
    experimental_wayland = resolve_wayland(os.environ, experimental_wayland)
    report = session_report(os.environ, experimental_wayland)
    if report['errors']:
        raise RuntimeError('\n'.join(report['errors']))
    state = root / 'session.json'
    with (root / 'launch.lock').open('a') as lock:
        if desktop.reopen_or_lock(lock, root, config_path, state, 'app'):
            return 0
        if (root.parent / '.uninstalling').exists():
            raise RuntimeError('This installation is being removed.')
        verify(config, root)
        if desktop.prefix_processes(root / 'prefix'):
            raise RuntimeError('This prefix has processes outside its managed session; stop those first.')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', 9010)) == 0:
                raise RuntimeError('Another G HUB agent owns port 9010. Quit that session before launching this one.')
        desktop.freeze_updates(root / 'prefix', root / 'logs')
        mode = 'wayland' if experimental_wayland else 'x11'
        command = ['/usr/bin/python3', str(SCRIPT), '--config', str(config_path), '--session', mode, '_session']
        if experimental_wayland:
            (root / 'logs/wayland-preflight.json').write_text(json.dumps(report, indent=2) + '\n')
        log = root / 'logs' / (time.strftime('%Y%m%d-%H%M%S') + f'-{os.getpid()}-desktop.log')
        busy = ('The shared Penguin LGHUB runtime is still in use by another launch or setup operation. '
                'Use that launcher\'s “Quit this G HUB session” action and let it finish before retrying. '
                f'Runtime: {config["linux_runtime"]}')
        with work_lock(Path(config['linux_runtime']), busy_message=busy), log.open('x') as stream:
            stream.write(f'Desktop session mode: {mode}\n')
            stream.flush()
            status = run_container_session(config_path, config, root, command, stream, feedback, experimental_wayland)
        if status:
            if status == 75:
                raise RuntimeError(f'G HUB could not open its window after one clean retry. '
                                   f'The session has stopped and your settings are preserved. Log: {log}')
            raise RuntimeError(f'Container session exited {status}. Log: {log}')
        return 0


def install_entry(config_path, destination, experimental_wayland=None, applications=None):
    def quote(s):
        return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    command = ' '.join(map(quote, (sys.executable, SCRIPT, '--config', config_path)))
    mode = 'auto' if experimental_wayland is None else ('wayland' if experimental_wayland else 'x11')
    command += ' --session ' + mode
    # Keep the icon with this installation, independent of the source checkout
    # and of the host's icon theme. Upgrading a launcher does not touch Wine.
    icon = config_path.parent / 'penguin-lghub.png'
    if not destination.exists():
        temporary = icon.with_suffix('.png.new')
        shutil.copyfile(ICON, temporary)
        temporary.replace(icon)
    icon_value = str(icon).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')
    name = 'Penguin-LGHUB'
    comment = 'Logitech G HUB with the private Wine runtime'
    actions = 'Check;Help;Logs;Stop;'
    content = (f'[Desktop Entry]\nType=Application\nName={name}\nComment={comment}\n'
               f'Exec={command} launch\nTerminal=false\nIcon={icon_value}\n'
               f'StartupNotify=true\nStartupWMClass=lghub.exe\nActions={actions}\n\n'
               f'[Desktop Action Check]\nName=Check setup\nExec={command} check --dialog\n\n'
               f'[Desktop Action Stop]\nName=Quit this G HUB session\nExec={command} stop\n')
    content += (f'\n[Desktop Action Help]\nName=Help\nExec={command} help\n'
                f'\n[Desktop Action Logs]\nName=Open logs\nExec={command} logs\n')
    paths = [destination] + ([applications / destination.name] if applications is not None else [])
    record_launchers(config_path, paths, content.encode(), applications)
    write_entry(destination, content.encode(), 0o755)
    if applications is not None:
        register_entry(destination, applications)


def write_entry(path, data, mode):
    """Remove our incomplete file if writing a new launcher fails."""
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    identity=os.fstat(fd)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        path.chmod(mode)
    except BaseException:
        try:
            current=path.lstat()
            if (current.st_dev,current.st_ino)==(identity.st_dev,identity.st_ino):path.unlink()
        except FileNotFoundError:pass
        raise


def register_entry(source, applications):
    """Let Plasma resolve the shortcut's application ID, icon and feedback."""
    applications.mkdir(parents=True, exist_ok=True)
    target = applications / source.name
    if target.resolve() == source.resolve():
        return
    data = source.read_bytes()
    if target.exists():
        if target.is_symlink() or target.read_bytes() != data:
            raise RuntimeError('An application entry with this name already exists: ' + str(target))
        return
    write_entry(target, data, 0o644)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'check', 'launch', 'stop', 'install-entry', '_session', 'help', 'logs', 'stage-frontend', 'stage-x11-restore', 'stage-desktop-init'))
    for name in ('candidate', 'linux-runtime', 'wine', 'test-tools', 'output', 'config', 'destination'):
        p.add_argument('--' + name, type=Path)
    p.add_argument('--dialog', action='store_true')
    p.add_argument('--x11-restore-module', type=Path, help='Verified optional Wine 10.20 restore driver')
    p.add_argument('--desktop-init-module', type=Path, help='Verified optional Wine desktop initialization guard')
    session_options = p.add_mutually_exclusive_group()
    session_options.add_argument('--session', choices=('auto', 'x11', 'wayland'), default='auto',
                                 help='Select the current login session automatically (default), or require a specific session')
    session_options.add_argument('--experimental-wayland', dest='session', action='store_const', const='wayland',
                                 help='Legacy alias for --session wayland; never grants input permissions')
    a = p.parse_args()
    experimental_wayland = {'auto': None, 'x11': False, 'wayland': True}[a.session]
    if os.geteuid() == 0:
        raise ValueError('Run this launcher as the desktop user, never root')
    if a.action == 'prepare':
        names = ('candidate', 'linux_runtime', 'wine', 'test_tools', 'output')
        if any(getattr(a, n) is None for n in names):
            p.error('prepare requires candidate, linux-runtime, wine, test-tools and output')
        prepared = prepare(*(getattr(a, n).expanduser().resolve() for n in names))
        if a.x11_restore_module:
            wine_x11_restore.stage(prepared, a.x11_restore_module.expanduser().absolute())
        if a.desktop_init_module:
            wine_desktop_init.stage(prepared, a.desktop_init_module.expanduser().absolute())
        print(prepared)
        return 0
    if not a.config:
        p.error('--config is required')
    config_path = a.config.expanduser().resolve(strict=True)
    root = config_path.parent
    config = json.loads(config_path.read_text())
    if a.action == 'stage-desktop-init':
        if not a.desktop_init_module:
            p.error('stage-desktop-init requires --desktop-init-module')
        verify(config, root)
        print(json.dumps(wine_desktop_init.stage(config_path, a.desktop_init_module.expanduser().absolute()), indent=2))
        return 0
    if a.action == 'stage-x11-restore':
        if not a.x11_restore_module:
            p.error('stage-x11-restore requires --x11-restore-module')
        verify(config, root)
        print(json.dumps(wine_x11_restore.stage(config_path, a.x11_restore_module.expanduser().absolute()), indent=2))
        return 0
    if a.action == 'stage-frontend':
        if not a.candidate:
            p.error('stage-frontend requires --candidate')
        print(json.dumps(stage_frontend(a.candidate.expanduser().resolve(), config_path), indent=2))
        return 0
    if a.action == 'check':
        verify(config, root)
        session = session_report(os.environ, experimental_wayland)
        report = {'runtime_integrity': 'passed', 'session_errors': session['errors'], 'session': session,
                  'logitech_hidraw': device_status(), 'end_to_end_qualified': False}
        print(json.dumps(report, indent=2))
        if a.dialog:
            show_message('Penguin LGHUB setup', json.dumps(report, indent=2))
        return int(bool(report['session_errors']))
    if a.action == 'help':
        print(wayland_session.HELP)
        show_message('Penguin-LGHUB', wayland_session.HELP)
        return 0
    if a.action == 'logs':
        subprocess.run(['xdg-open', str(root / 'logs')], check=True)
        return 0
    if a.action == 'stop':
        desktop.stop(config_path, root / 'session.json')
        return 0
    if a.action == 'install-entry':
        if not a.destination:
            p.error('--destination is required')
        verify(config, root)
        applications = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications'
        install_entry(config_path, a.destination.expanduser().resolve(), experimental_wayland, applications)
        return 0
    if a.action == '_session':
        return worker(config, root, root / 'session.json', resolve_wayland(os.environ, experimental_wayland))
    try:
        return launch(config_path, config, root, experimental_wayland)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Desktop-entry stderr may disappear. Preserve the call site so a
        # future EAGAIN can be distinguished from unrelated socket/fork errors.
        with (root / 'logs/launcher-errors.log').open('a') as log:
            log.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n' + traceback.format_exc() + '\n')
        raise


if __name__ == '__main__':
    os.umask(0o077)
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        if ('launch' in sys.argv or '--dialog' in sys.argv) and not os.environ.get('GHUB_LAUNCHER_NO_DIALOG'):
            show_message('Penguin LGHUB', str(error), error=True)
        sys.exit(1)
