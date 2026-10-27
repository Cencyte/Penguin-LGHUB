#!/usr/bin/env python3
"""User-operated X11 launcher for the pinned Wine/Linux runtime.

prepare creates a separate working prefix; check never connects to a display.
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

import linux_runtime as linux
import linux_probe
from prepare_clean_prefix import APP, verify_prepared
from windows_media_acquire import ROOT, digest_file, space_check, work_lock
from wine_runtime import runtime_environment

sys.path.insert(0, str(ROOT / 'tools/wine'))
import tested_desktop_launch as desktop
from prepare_tested_desktop import desktop_environment

SCRIPT = Path(__file__).resolve()
# Reuse the tested supervisor's PID/start-time/boot-id validation and its
# authenticated reopen endpoint. --share-pid keeps its host identity valid.
desktop.SCRIPT = SCRIPT


def session_environment(source):
    env = linux.clean_environment()
    for key in ('DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'XDG_SESSION_TYPE',
                'DBUS_SESSION_BUS_ADDRESS', 'PULSE_SERVER', 'LANG', 'LC_ALL'):
        if source.get(key):
            env[key] = source[key]
    return env


def session_errors(env):
    errors = []
    if env.get('XDG_SESSION_TYPE') != 'x11':
        errors.append('Select a Plasma (X11) login session. Wayland/Xwayland desktop key output is not supported.')
    if not re.fullmatch(r':\d+(?:\.\d+)?', env.get('DISPLAY', '')):
        errors.append('Launch from the local X11 desktop, not an SSH-forwarded display.')
    return errors


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
    # The generated application is immutable; settings/registry remain writable.
    # Apply this *inside* pressure-vessel, after the writable output mount.
    bwrap = root / linux.DEPOT / 'pressure-vessel/libexec/steam-runtime-tools-0/srt-bwrap'
    boundary = args.index('--') + 1
    app = output / 'prefix' / APP
    args[boundary:boundary] = [str(bwrap), '--bind', '/', '/', '--dev-bind', '/dev', '/dev',
                               '--ro-bind', str(app), str(app), '--']
    return list(map(str, args))


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
    space_check(output.parent, 4 * 1024**3)
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
             if name.startswith(('prefix/' + APP + '/', 'prefix/drive_c/windows/system32/'))}
    config = {'schema': 1, 'kind': 'penguin-desktop-runtime', 'linux_runtime': str(linux_root),
              'wine': str(wine), 'test_tools': str(test_tools), 'environment': desktop_environment(receipt['runtime_environment']),
              'packaged_runtime': receipt['packaged_runtime'], 'freeze_updates': True,
              'candidate_manifest_sha256': digest_file(candidate / 'clean-prefix.json')['sha256'],
              'fixed_files': fixed, 'end_to_end_qualified': False}
    (output / 'desktop.json').write_text(json.dumps(config, indent=2) + '\n')
    desktop.freeze_updates(output / 'prefix', output / 'logs')
    return output / 'desktop.json'


def verify(config, root):
    if config.get('schema') != 1 or config.get('kind') != 'penguin-desktop-runtime':
        raise ValueError('Not a packaged desktop installation')
    for name, sha in config['fixed_files'].items():
        target = root / name
        if not target.resolve().is_relative_to(root) or digest_file(target)['sha256'] != sha:
            raise ValueError('Desktop component changed: ' + name)
    linux.verify(Path(config['linux_runtime']))
    linux_probe.verify(Path(config['test_tools']))
    linux_probe.matching_runtime(config, Path(config['wine']))


def worker(config, root, state):
    expected = json.loads(linux.LOCK.read_text())['build_id']
    if f'BUILD_ID="{expected}"' not in Path('/etc/os-release').read_text():
        raise RuntimeError('Desktop worker must run inside the pinned Linux userspace')
    wine, linux_root, test_tools = (Path(config[k]) for k in ('wine', 'linux_runtime', 'test_tools'))
    env = runtime_environment(wine, root / 'prefix', os.environ)
    env.update(config['environment'])
    env['PATH'] = str(wine / 'usr/bin') + ':' + str(test_tools / 'node/node-v22.21.0-linux-x64/bin') + ':/usr/bin:/bin'
    # Keep pressure-vessel's imported driver paths ahead of our supplementary
    # non-graphics libraries. Never import the caller's arbitrary LD_* values.
    env['LD_LIBRARY_PATH'] = ':'.join(filter(None, (os.environ.get('LD_LIBRARY_PATH'), linux.library_path(linux_root, wine))))
    os.environ.clear()
    os.environ.update(env)
    return desktop.session(config, root, state)


def launch(config_path, config, root):
    errors = session_errors(os.environ)
    if errors:
        raise RuntimeError('\n'.join(errors))
    state = root / 'session.json'
    with (root / 'launch.lock').open('a') as lock:
        if desktop.reopen_or_lock(lock, root, config_path, state, 'app'):
            return 0
        verify(config, root)
        if desktop.prefix_processes(root / 'prefix'):
            raise RuntimeError('This prefix has processes outside its managed session; stop those first.')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', 9010)) == 0:
                raise RuntimeError('Another G HUB agent owns port 9010. Quit that session before launching this one.')
        desktop.freeze_updates(root / 'prefix', root / 'logs')
        command = ['/usr/bin/python3', str(SCRIPT), '--config', str(config_path), '_session']
        log = root / 'logs' / (time.strftime('%Y%m%d-%H%M%S') + f'-{os.getpid()}-desktop.log')
        with work_lock(Path(config['linux_runtime'])), log.open('x') as stream:
            status = subprocess.run(container_command(config, root, command), env=session_environment(os.environ),
                                    stdout=stream, stderr=subprocess.STDOUT).returncode
        if status:
            raise RuntimeError(f'Container session exited {status}. Log: {log}')
        return 0


def install_entry(config_path, destination):
    def quote(s):
        return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    command = ' '.join(map(quote, (sys.executable, SCRIPT, '--config', config_path)))
    content = ('[Desktop Entry]\nType=Application\nName=Penguin LGHUB — Container test\n'
               'Comment=Private Wine and Linux runtime; select an X11 session first\n'
               f'Exec={command} launch\nTerminal=false\nIcon=input-mouse\nActions=Check;Stop;\n\n'
               f'[Desktop Action Check]\nName=Check setup\nExec={command} check --dialog\n\n'
               f'[Desktop Action Stop]\nName=Quit this G HUB session\nExec={command} stop\n')
    with destination.open('x') as stream:
        stream.write(content)
    destination.chmod(0o755)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'check', 'launch', 'stop', 'install-entry', '_session'))
    for name in ('candidate', 'linux-runtime', 'wine', 'test-tools', 'output', 'config', 'destination'):
        p.add_argument('--' + name, type=Path)
    p.add_argument('--dialog', action='store_true')
    a = p.parse_args()
    if os.geteuid() == 0:
        raise ValueError('Run this launcher as the desktop user, never root')
    if a.action == 'prepare':
        names = ('candidate', 'linux_runtime', 'wine', 'test_tools', 'output')
        if any(getattr(a, n) is None for n in names):
            p.error('prepare requires candidate, linux-runtime, wine, test-tools and output')
        print(prepare(*(getattr(a, n).expanduser().resolve() for n in names)))
        return 0
    if not a.config:
        p.error('--config is required')
    config_path = a.config.expanduser().resolve(strict=True)
    root = config_path.parent
    config = json.loads(config_path.read_text())
    if a.action == 'check':
        verify(config, root)
        report = {'runtime_integrity': 'passed', 'session_errors': session_errors(os.environ),
                  'logitech_hidraw': device_status(), 'end_to_end_qualified': False}
        print(json.dumps(report, indent=2))
        if a.dialog and shutil.which('kdialog'):
            subprocess.run(['kdialog', '--title', 'Penguin LGHUB setup', '--msgbox', json.dumps(report, indent=2)], check=False)
        return int(bool(report['session_errors']))
    if a.action == 'stop':
        desktop.stop(config_path, root / 'session.json')
        return 0
    if a.action == 'install-entry':
        if not a.destination:
            p.error('--destination is required')
        verify(config, root)
        install_entry(config_path, a.destination.expanduser().resolve())
        return 0
    if a.action == '_session':
        return worker(config, root, root / 'session.json')
    return launch(config_path, config, root)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        if os.environ.get('DISPLAY') and 'launch' in sys.argv and shutil.which('kdialog'):
            subprocess.run(['kdialog', '--title', 'Penguin LGHUB', '--sorry', str(error)], check=False)
        sys.exit(1)
