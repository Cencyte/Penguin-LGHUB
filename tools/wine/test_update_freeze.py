#!/usr/bin/env python3
"""Exercise native update policy in an isolated network namespace and Xvfb.

Run as the normal user inside a fresh network namespace with loopback up.
Requires a disposable, stopped prepared installation; leaves the live desktop
and host network alone. No downloads are possible in this namespace.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import time

from tested_desktop_launch import check, digest, prefix_processes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--performance', action='store_true')
    parser.add_argument('--reopen', action='store_true', help='Also exercise native frontend reopen and background-only recovery')
    parser.add_argument('--legacy-reopen', action='store_true', help='Migrate a running old launcher and test reopen')
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    prefix = root / 'prefix'
    config = json.loads((root / 'launch.json').read_text())
    check(config, root)
    host_net = subprocess.check_output(['sudo', '-n', 'readlink', '/proc/1/ns/net'], text=True).strip()
    if (os.readlink('/proc/self/ns/net') == host_net or
            not (prefix / '.overlay-trace-disposable').exists() or
            prefix_processes(prefix) or not config.get('freeze_updates')):
        parser.error('Use a stopped disposable frozen installation in a private network namespace')
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'network_isolated':True, 'checks':{}}
    archive = prefix / 'drive_c/Program Files/LGHUB/resources/app.asar'
    archive_before = digest(archive)
    sentinel = prefix / 'drive_c/Program Files/LGHUB/freeze-test-sentinel'
    sentinel.write_text('original\n')
    x = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1280x900x24', '-nolisten', 'tcp'],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    manager = None
    command = ['python3', str(root / 'runner/tested_desktop_launch.py'), '--config', str(root / 'launch.json')]
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(x.stdout, selectors.EVENT_READ)
            if not selector.select(10):
                raise RuntimeError('Xvfb startup timed out')
        display = x.stdout.readline().decode().strip()
        if not display.isdecimal():
            raise RuntimeError('Invalid owned display')
        env = dict(os.environ, DISPLAY=':' + display, GHUB_LAUNCHER_NO_DIALOG='1')
        for key in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
            env.pop(key, None)
        with (args.output / 'launcher.log').open('w') as log:
            started = time.time()
            manager = subprocess.Popen(command + ['performance' if args.performance else 'app'], env=env, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 110
            while time.monotonic() < deadline:
                if manager.poll() is not None:
                    raise RuntimeError('Candidate launcher exited')
                snap = root / 'logs/startup-snapshot.json'
                if snap.exists() and snap.stat().st_mtime >= started:
                    break
                time.sleep(.5)
            else:
                raise RuntimeError('Candidate agent did not become ready')
            state = json.loads((root / 'logs/session.json').read_text())
            pid = state['pid']
            # Enter only this test session's mount namespace; never target the
            # user's live Wine session. Preserve the test's network namespace.
            ns = ['sudo', '-n', 'nsenter', '-t', str(pid), '-m', '--', 'setpriv',
                  '--reuid', str(os.getuid()), '--regid', str(os.getgid()), '--init-groups']
            write_probe = subprocess.run(ns + ['python3', '-c',
                'import errno,sys\ntry:\n open(sys.argv[1],"w").write("changed")\n'
                'except OSError as e:\n sys.exit(0 if e.errno==errno.EROFS else 2)\n'
                'sys.exit(1)', str(sentinel)], capture_output=True, text=True)
            report['checks']['application_read_only'] = write_probe.returncode == 0 and sentinel.read_text() == 'original\n'
            if config.get('frontend_archive'):
                private_hash = subprocess.check_output(ns + ['sha256sum', str(archive)], text=True).split()[0]
                report['checks']['private_frontend_mounted'] = private_hash == config['sha256'][config['frontend_archive']]
                # Opening for write without writing bytes must fail with EROFS.
                ro = subprocess.run(ns + ['python3', '-c',
                    'import errno,sys\ntry:\n open(sys.argv[1],"r+b").close()\n'
                    'except OSError as e:\n sys.exit(0 if e.errno==errno.EROFS else 2)\nsys.exit(1)',
                    str(archive)], capture_output=True, timeout=10)
                report['checks']['private_frontend_read_only'] = ro.returncode == 0
                report['checks']['host_frontend_unchanged'] = digest(archive) == archive_before and private_hash != archive_before
            block = subprocess.run(ns + ['env', 'WINEPREFIX=' + str(prefix), 'DISPLAY=' + env['DISPLAY'],
                'WINEDLLOVERRIDES=lghub_software_manager.exe=d', 'WINEDEBUG=-all', 'wine',
                str(prefix / 'drive_c/Program Files/LGHUB/lghub_software_manager.exe'), '--update'],
                capture_output=True, text=True, timeout=15)
            report['manager_attempt_exit'] = block.returncode
            report['checks']['manager_blocked'] = block.returncode == 53
            query = '''const w=new WebSocket('ws://127.0.0.1:9010','json');
const timer=setTimeout(()=>process.exit(2),10000);
w.onopen=()=>w.send(JSON.stringify({msgId:'freeze-check',verb:'GET',path:'/updates/periodic_check',origin:'frontend'}));
w.onmessage=e=>{const m=JSON.parse(e.data);if(m.msgId==='freeze-check'){require('fs').writeFileSync(1,JSON.stringify(m));process.exit(m.result?.code==='SUCCESS'?0:1)}};
w.onerror=()=>process.exit(3);'''
            # Opening the native frontend can restart the agent during its
            # handoff to the tray. Wait for the actual response, not a stale
            # startup snapshot from before that handoff.
            for attempt in range(6):
                result = subprocess.run(['node', '-e', query], capture_output=True, text=True, timeout=15)
                if result.stdout.strip():
                    break
                report.setdefault('query_retries', []).append({'exit':result.returncode, 'stderr':result.stderr})
                time.sleep(1)
            if not result.stdout.strip():
                raise RuntimeError('Native update policy query failed: ' + str(report['query_retries']))
            response = json.loads(result.stdout)
            report['native_periodic_response'] = response
            payload = response.get('payload', {})
            report['checks']['native_policy_disabled'] = (result.returncode == 0 and
                payload.get('checkPeriodically') is False and payload.get('downloadAutomatically') is False)
            native_set = subprocess.run(['node', str(Path(__file__).with_name('update_policy.cjs')),
                                         '--disable', str(args.output / 'native-set.json')],
                                        capture_output=True, text=True, timeout=20)
            report['checks']['native_set_verified'] = native_set.returncode == 0
            time.sleep(12)
            processes = prefix_processes(prefix)
            report['processes'] = processes
            report['checks']['updater_service_alive'] = any('lghub_updater.exe' in p[1] for p in processes)
            report['checks']['no_software_manager'] = not any('lghub_software_manager.exe' in p[1] for p in processes)
            titles = subprocess.run(['xwininfo', '-root', '-tree'], env=env, capture_output=True, text=True, check=True).stdout
            (args.output / 'windows.txt').write_text(titles)
            report['checks']['no_installer_dialog'] = 'G HUB Installer' not in titles and 'LGHUB Installer' not in titles
            if args.performance:
                recordings = [p for p in (root / 'logs').glob('*-color-performance.jsonl') if p.stat().st_mtime >= started]
                report['checks']['passive_monitor_started'] = bool(recordings)
            if args.legacy_reopen:
                from test_desktop_reopen import exercise_legacy_reopen
                manager = exercise_legacy_reopen(root, command, env, args.output, report['checks'], manager)
            elif args.reopen:
                from test_desktop_reopen import exercise_reopen
                exercise_reopen(root, command, env, args.output, report['checks'], performance=args.performance)
            subprocess.run(command + ['stop'], env=env, check=True)
            report['checks']['clean_stop'] = manager.wait(timeout=35) == 0
            settings = json.loads((prefix / 'drive_c/ProgramData/LGHUB/periodic_check.json').read_text())
            report['checks']['policy_persisted'] = (settings.get('checkPeriodically') is False and settings.get('downloadAutomatically') is False)
            if config.get('frontend_archive'):
                report['checks']['host_frontend_unchanged_after_exit'] = digest(archive) == archive_before
    finally:
        try:
            if manager and manager.poll() is None:
                subprocess.run(command + ['stop'], env=env, check=False)
                manager.wait(timeout=55)
        finally:
            x.terminate()
            x.wait(timeout=10)
            (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['checks'], indent=2))
    return 0 if all(report['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
