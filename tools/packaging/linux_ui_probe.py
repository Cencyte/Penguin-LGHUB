#!/usr/bin/env python3
"""Check first-run UI in a new private Xvfb/network/device namespace.

This observes fonts, installed files, Desktop/default-profile initialization
and the readable first-run prompt. It does not click through the user's choices.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import socket
import subprocess
import sys
import time

import desktop_runtime as desktop
import linux_probe
import linux_runtime as linux
from probe_clean_agent import runtime_images
from windows_media_acquire import ROOT, external_path, work_lock
from tray_registration import PrivateTray
from server_sync import verify_server_sync

SCRIPT = Path(__file__).resolve()


def worker(output):
    expected = json.loads(linux.LOCK.read_text())['build_id']
    if (f'BUILD_ID="{expected}"' not in Path('/etc/os-release').read_text()
            or socket.if_nameindex() != [(1, 'lo')]
            or list(Path('/dev').glob('hidraw*')) or Path('/dev/input').exists()
            or os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise RuntimeError('UI probe requires the isolated Linux runtime without a live desktop or devices')
    verify_server_sync()
    config = json.loads((output / 'desktop.json').read_text())
    root, wine, test_tools = (Path(config[k]) for k in ('linux_runtime', 'wine', 'test_tools'))
    env = linux_probe.probe_environment(root, wine, test_tools, output, config['environment'])
    env.update(LIBGL_ALWAYS_SOFTWARE='1', LP_NUM_THREADS='2', PENGUIN_ISOLATED_UI_PROBE='1')
    os.environ['PENGUIN_ISOLATED_UI_PROBE'] = '1'
    tray = None
    Path('/tmp/.X11-unix').mkdir(exist_ok=True)
    with (output / 'xvfb.log').open('x') as log:
        x = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1280x900x24',
                              '-nolisten', 'tcp'], env=env,
                             stdout=subprocess.PIPE, stderr=log)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(x.stdout, selectors.EVENT_READ)
            if not selector.select(15):
                raise RuntimeError('Owned Xvfb did not start')
        display = x.stdout.readline().decode().strip()
        if not display.isdecimal():
            raise RuntimeError('Invalid Xvfb display')
        env.update(DISPLAY=':' + display, XDG_SESSION_TYPE='x11')
        tray = PrivateTray(env['DISPLAY'])
        desktop.configure_fonts(output, env)
        app = output / 'prefix' / desktop.APP
        with (output / 'agent.log').open('x') as log:
            agent = subprocess.Popen(['wine', str(app / 'lghub_agent.exe')], cwd=app,
                                     env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ready = subprocess.run(['node', str(ROOT / 'tools/wine/agent_snapshot.cjs')], env=env,
                                   capture_output=True, text=True, timeout=15)
            if ready.returncode == 0:
                break
            if agent.poll() is not None:
                raise RuntimeError('Agent exited before readiness')
            time.sleep(1)
        else:
            raise RuntimeError('Agent did not become ready')
        images = runtime_images(wine)
        with (output / 'observer.log').open('x') as log:
            observer = subprocess.Popen(['node', str(ROOT / 'tools/packaging/frontend_probe.cjs'), str(output)],
                                        env=env, stdout=log, stderr=subprocess.STDOUT)
        with (output / 'frontend.log').open('x') as log:
            subprocess.Popen(['wine', str(app / 'lghub.exe'), '--disable-gpu',
                              '--remote-debugging-port=9224', '--remote-debugging-address=127.0.0.1'],
                             cwd=app, env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 150
        while observer.poll() is None and time.monotonic() < deadline:
            tray.poll()
            time.sleep(.1)
        status = observer.wait(timeout=1)
        deadline = time.monotonic() + 30
        while not tray.poll() and time.monotonic() < deadline:
            time.sleep(.1)
        processes = desktop.desktop.prefix_processes(output / 'prefix')
        native_running = any('lghub_system_tray.exe' in exe for _, exe in processes)
        report = json.loads((output / 'ui-verification.json').read_text())
        report['checks'].update(native_tray_running=native_running, xembed_dock_received=bool(tray.docked))
        report['passed'] = bool(report['passed'] and native_running and tray.docked)
        report['scope'] = ('Isolated frontend and native tray startup/XEmbed registration; '
                           'no Plasma popup/input, physical devices or host GPU validation')
        report['tray'] = {'xembed_dock_requests': len(tray.docked), 'native_process_running': native_running}
        report.update(linux_runtime_build=expected, host_wine_hidden=True,
                      wine_synchronization='server',
                      ntsync_mask_present=Path('/dev/ntsync').exists(),
                      mapped_runtime_image_count=len(images), live_desktop_used=False,
                      physical_devices_exposed=False, isolated_network=True,
                      end_to_end_qualified=False)
        (output / 'ui-verification.json').write_text(json.dumps(report, indent=2) + '\n')
        return status or int(not report['passed'])
    finally:
        subprocess.run(['wineserver', '-k'], env=env, capture_output=True, timeout=15)
        subprocess.run(['wineserver', '-w'], env=env, capture_output=True, timeout=15)
        if tray:
            tray.close()
        x.terminate()
        x.wait(timeout=10)


def probe_command(root, wine, test_tools, output):
    command = ['/usr/bin/python3', str(SCRIPT), 'worker', '--output', str(output)]
    args = linux.isolated_command(root, output,
        linux_probe.test_command(root, test_tools, command), freeze_app=True,
        read_only_paths=(root, wine, test_tools))
    # Challenge the production synchronization boundary on hosts that provide
    # NTSYNC. Otherwise a synthetic /dev silently avoids this regression.
    # Only this logical synchronization device enters the outer test namespace;
    # server_sync masks it before Wine starts. No HID/input/GPU is exposed.
    if Path('/dev/ntsync').exists():
        boundary = args.index('--')
        args[boundary:boundary] = ['--dev-bind', '/dev/ntsync', '/dev/ntsync']
    return args


def probe(candidate, root, wine, test_tools, output):
    with work_lock(root):
        desktop.prepare(candidate, root, wine, test_tools, output)
        with (output / 'probe.log').open('x') as log:
            result = subprocess.run(probe_command(root, wine, test_tools, output), env=linux.clean_environment(),
                stdout=log, stderr=subprocess.STDOUT, timeout=240)
    report = output / 'ui-verification.json'
    if report.exists():
        print(report.read_text())
    result.check_returncode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('probe', 'worker'))
    for name in ('candidate', 'linux-runtime', 'wine', 'test-tools', 'output'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run as a normal user')
    if not args.output:
        parser.error('--output is required')
    if args.action == 'worker':
        return worker(args.output.resolve(strict=True))
    names = ('candidate', 'linux_runtime', 'wine', 'test_tools', 'output')
    if any(getattr(args, n) is None for n in names):
        parser.error('probe requires candidate, linux-runtime, wine, test-tools and output')
    probe(*(external_path(getattr(args, n)) for n in names))
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(main())
