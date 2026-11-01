#!/usr/bin/env python3
"""Prove KWin focus identity in a disposable headless Wayland/Xwayland session.

Installs nothing and never loads scripts into the real desktop. Existing Qt6
development files, KWin, Xwayland, gdbus and bubblewrap are required. This tests
the focus source and existing mailbox format, not G HUB or physical devices.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/penguin-profile-probe')
sys.path.insert(0, str(HERE.parent / 'wine'))
from native_profiles import HEADER, Mailbox, ProfileObserver, process_identity, wine_path


def container_boundary(pid, output, check, result):
    """Exercise the real runtime transport without starting Wine or G HUB."""
    import linux_runtime as linux
    runtime = Path((PRIVATE/'linux-runtime-path').read_text())
    output.mkdir(mode=0o700)
    (output/'home').mkdir(mode=0o700)
    script = PRIVATE/'container-focus.js'
    # A separate script emits one event with a distinct reason. The production
    # observer must create scripts on a path the host compositor can also read.
    script.write_text((PRIVATE/'focus.js').read_text().replace('"initial"', '"container-initial"')
                      .replace('"Observe"', '"ObserveBoundary"')
                      .split('// Heartbeats')[0])
    handoff = Mailbox(PRIVATE)
    executable, _ = process_identity(pid)
    handoff.write(wine_path(executable))
    args = linux.container_command(runtime, output,
        [str(PRIVATE/'bus-probe'), str(pid), str(PRIVATE/'wayland-one'), str(script), str(handoff.path)], (PRIVATE, runtime))
    args[args.index('--unshare-pid')] = '--share-pid'
    variable = next(i for i, arg in enumerate(args) if arg.startswith('--variable-dir='))
    args[variable] = '--variable-dir=' + str(output/'var')
    (output/'command.json').write_text(json.dumps(args, indent=2)+'\n')
    env = linux.clean_environment()
    for key in ('DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR', 'XDG_SESSION_TYPE',
                'PENGUIN_PRIVATE_PROFILE_PROBE'):
        env[key] = os.environ[key]
    try:
        with (output/'transport.log').open('w') as log:
            child = subprocess.run(args, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=30)
        log = (output/'transport.log').read_text(errors='replace')
        result['container_capabilities'] = {
            'desktop_process_executable_readable': 'HOST_PROCESS_PATH_READABLE' in log,
            'desktop_process_executable_read_failed': 'HOST_PROCESS_PATH_UNREADABLE' in log,
            'compositor_bus_and_script': 'COMPOSITOR_BUS_AND_SCRIPT_OK' in log,
            'identity_resolution_design': 'Resolve desktop executables outside the runtime user namespace'
        }
        check('packaged runtime connects compositor bus and loads host-readable script',
              child.returncode == 0 and 'COMPOSITOR_BUS_AND_SCRIPT_OK' in log,
              exit_code=child.returncode)
        check('runtime reads host-resolved executable from shared mailbox',
              'HOST_MAILBOX_READABLE_AND_CORRECT' in log)
        event = next((x for x in records(PRIVATE/'receiver.log')
                      if x.get('reason') == 'container-initial'), None)
        check('script loaded from container executes in host KWin',
              event is not None and event.get('pid') == pid, event=event)
    finally:
        handoff.cleanup()
        # Disposable materialized runtime only; preserve source runtime and logs.
        shutil.rmtree(output/'var', ignore_errors=True)
        dbus('/Scripting', 'org.kde.kwin.Scripting.unloadScript', 'penguin-private-container-script')


def records(path):
    result = []
    for line in path.read_text(errors='replace').splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # Qt diagnostics may share stderr; incomplete last write is retried.
    return result


def wait_record(path, condition, child, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for event in records(path):
            if condition(event):
                return event
        if child.poll() is not None:
            raise RuntimeError(f'{path.name}: process exited {child.returncode}')
        time.sleep(.03)
    raise TimeoutError(f'{path.name}: expected event absent')


def dbus(path, method, *arguments, destination='org.kde.KWin'):
    return subprocess.check_output(['gdbus', 'call', '--session', '--dest', destination,
                                   '--object-path', path, '--method', method, *arguments],
                                  text=True, stderr=subprocess.STDOUT, timeout=5).strip()


def inside():
    if (os.environ.get('PENGUIN_PRIVATE_PROFILE_PROBE') != '1'
            or os.environ.get('XDG_RUNTIME_DIR') != str(PRIVATE/'run')
            or Path('/proc/1/comm').read_text().strip() not in ('bwrap', 'dbus-run-sessio')):
        raise RuntimeError('Private namespace required')
    result = {'passed': False, 'live_desktop_touched': False,
              'scope': 'KWin focus -> validated Linux executable -> existing mailbox; no G HUB or hardware',
              'checks': []}
    children = []; logs = []; script_loaded = False; mailbox = None
    name = 'penguin-private-profile-probe'

    def spawn(binary, args, platform='offscreen'):
        log = (PRIVATE / (binary + '.log')).open('w')
        logs.append(log)
        child = subprocess.Popen([str(PRIVATE/binary), *args],
                                 env=dict(os.environ, QT_QPA_PLATFORM=platform),
                                 stdout=log, stderr=subprocess.STDOUT)
        children.append(child)
        return child

    def check(case, passed, **evidence):
        result['checks'].append({'case': case, 'passed': bool(passed), **evidence})
        if not passed:
            raise AssertionError(case)

    try:
        if (PRIVATE/'protected-compositor').exists():
            from profile_dbus import Bus
            bus = Bus()
            try:
                owner = bus.owner('org.kde.KWin')
                pid = bus.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                               'org.freedesktop.DBus', 'GetConnectionUnixProcessID', owner)[0]
                try:
                    os.readlink(f'/proc/{pid}/exe')
                except PermissionError:
                    denied = True
                else:
                    denied = False
                check('protected compositor denies procfs executable access', denied)
            finally:
                bus.close()
        receiver = spawn('receiver', ['--receiver'])
        events = PRIVATE/'receiver.log'
        wait_record(events, lambda x: x.get('kind') == 'ready', receiver)
        first = spawn('wayland-one', ['--window', 'org.penguin.ProbeOne'], 'wayland')
        wait_record(PRIVATE/'wayland-one.log', lambda x: x.get('kind') == 'active', first)
        answer = dbus('/Scripting', 'org.kde.kwin.Scripting.loadScript',
                      str(PRIVATE/'focus.js'), name)
        script_id = int(re.fullmatch(r'\((-?\d+),\)', answer).group(1))
        check('ephemeral KWin script loaded', script_id >= 0)
        script_loaded = True
        dbus(f'/Scripting/Script{script_id}', 'org.kde.kwin.Script.run')
        initial = wait_record(events, lambda x: x.get('reason') == 'initial', receiver)
        check('initial native Wayland foreground snapshot', initial.get('pid') == first.pid,
              event=initial)
        heartbeat = wait_record(events, lambda x: x.get('reason') == 'heartbeat', receiver)
        check('heartbeat without focus changes', heartbeat.get('pid') == first.pid)
        forged = dbus('/Focus', 'org.penguinLGHUB.FocusProbe.Observe',
                      json.dumps({'protocol': 1, 'sequence': 999999, 'pid': first.pid}),
                      destination='org.penguinLGHUB.FocusProbe')
        check('non-KWin sender rejected', forged == '(false,)')
        if (PRIVATE/'linux-runtime-path').exists():
            container_boundary(first.pid, PRIVATE/'container', check, result)
        mailbox = Mailbox(PRIVATE)

        def focus_event(child, binary, last_sequence):
            event = wait_record(events, lambda x: x.get('kind') == 'focus'
                                and x.get('sequence', 0) > last_sequence
                                and x.get('reason') == 'activation' and x.get('pid') == child.pid,
                                receiver)
            executable, start_time = process_identity(child.pid)
            check(f'{binary}: compositor PID resolves actual executable',
                  executable == str(PRIVATE/binary), pid=child.pid, executable=executable,
                  process_start_time=start_time, event=event)
            translated = wine_path(executable)
            mailbox.write(translated)
            packet = mailbox.path.read_bytes()
            header = HEADER.unpack(packet[:HEADER.size])
            check(f'{binary}: compatible with existing authenticated mailbox',
                  header[0] == b'PGFOCUS1' and header[1].decode() == mailbox.token
                  and packet[HEADER.size:].decode() == translated)
            return event

        second = spawn('wayland-two', ['--window', 'org.penguin.ProbeTwo'], 'wayland')
        native = focus_event(second, 'wayland-two', heartbeat['sequence'])
        xclient = spawn('xwayland-one', ['--window', 'org.penguin.ProbeX11'], 'xcb')
        mixed = focus_event(xclient, 'xwayland-one', native['sequence'])
        xclient.terminate(); xclient.wait(timeout=5)
        restored = focus_event(second, 'wayland-two', mixed['sequence'])
        second.terminate(); second.wait(timeout=5)
        restored = focus_event(first, 'wayland-one', restored['sequence'])
        first.terminate(); first.wait(timeout=5)
        empty = wait_record(events, lambda x: x.get('kind') == 'focus'
                            and x.get('sequence', 0) > restored['sequence'] and x.get('pid') == 0,
                            receiver)
        check('no foreground window reported explicitly', empty['window'] == '', event=empty)
        answer = dbus('/Scripting', 'org.kde.kwin.Scripting.unloadScript', name)
        script_loaded = False
        time.sleep(.2)
        check('ephemeral script unloaded', answer == '(true,)' and
              dbus('/Scripting', 'org.kde.kwin.Scripting.isScriptLoaded', name) == '(false,)')
        count = len(records(events))
        time.sleep(1.1)
        check('unloading also stops heartbeat', len(records(events)) == count)
        if (PRIVATE/'production-observer').exists():
            observer = ProfileObserver(PRIVATE, wayland=True)
            try:
                # Only the source observer is tested here; this simulated ready
                # reply is explicitly not an agent/profile-selection E2E.
                observer.mailbox.path.with_suffix('.bin.status').write_text(observer.mailbox.token+' 0 ready\n')
                observer.start()
                for binary, platform in [('wayland-one','wayland'),('xwayland-one','xcb')]:
                    child = spawn(binary, ['--window', 'org.penguin.ProductionProbe'], platform)
                    end = time.monotonic()+10
                    expected = wine_path(PRIVATE/binary)
                    while observer.mailbox.current != expected:
                        observer.check()
                        if child.poll() is not None or time.monotonic()>end:
                            raise RuntimeError('Production observer missed '+binary)
                        time.sleep(.05)
                    check('production observer resolves '+platform+' executable', True)
                plugin = observer.source.plugin
                observer.close()
                check('production observer removes its KWin script',
                      dbus('/Scripting', 'org.kde.kwin.Scripting.isScriptLoaded', plugin) == '(false,)')
            finally:
                observer.close(); observer.mailbox.cleanup()
        result['passed'] = True
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        if script_loaded:
            try:
                dbus('/Scripting', 'org.kde.kwin.Scripting.unloadScript', name)
            except Exception as exc:
                result['cleanup_error'] = str(exc)
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=3)
        for log in logs:
            log.close()
        if mailbox:
            mailbox.close(); mailbox.cleanup()
        result['passed_checks'] = sum(x['passed'] for x in result['checks'])
        (PRIVATE/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return 0 if result['passed'] else 1


def run(output, linux_runtime=None, production=False, protected=False):
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    if production:
        (output/'production-observer').touch()
    if protected:
        (output/'protected-compositor').touch()
        subprocess.run(['gcc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        str(HERE/'wayland_profile_protected.c'),
                        '-o', str(output/'protected-compositor.so')], check=True)
    for name in ('home', 'config', 'run'):
        (output/name).mkdir(mode=0o700)
    versions = subprocess.run(['kwin_wayland', '--version'], capture_output=True, text=True)
    (output/'versions.txt').write_text(versions.stdout + versions.stderr)
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs',
                                                'Qt6Widgets', 'Qt6DBus'], text=True))
    moc_dir = subprocess.check_output(['pkg-config', '--variable=libexecdir', 'Qt6Core'], text=True).strip()
    moc = Path(moc_dir)/'moc'
    subprocess.run([str(moc), str(HERE/'wayland_profile_probe.cpp'),
                    '-o', str(output/'wayland_profile_probe.moc')], check=True)
    subprocess.run(['g++', '-std=c++17', '-fPIC', '-Wall', '-Wextra', '-I', str(output),
                    str(HERE/'wayland_profile_probe.cpp'), '-o', str(output/'receiver'), *flags], check=True)
    if linux_runtime:
        (output/'linux-runtime-path').write_text(str(linux_runtime.resolve()))
        flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'dbus-1'], text=True))
        subprocess.run(['gcc', '-Wall', '-Wextra', '-Werror', str(HERE/'wayland_profile_bus_probe.c'),
                        '-o', str(output/'bus-probe'), *flags], check=True)
    for name in ('wayland-one', 'wayland-two', 'xwayland-one'):
        shutil.copy2(output/'receiver', output/name)
    shutil.copy2(HERE/'wayland_profile_probe.js', output/'focus.js')
    (output/'session').write_text('#!/bin/sh\nexec /usr/bin/python3 ' +
                                shlex.quote(str(Path(__file__).resolve())) + ' --inside\n')
    (output/'session').chmod(0o700)
    args = ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/tmp', '--perms', '1777',
            '--dir', '/tmp/.X11-unix', '--bind', str(output), str(PRIVATE), '--tmpfs', '/run',
            '--dev', '/dev', '--proc', '/proc', '--unshare-all', '--die-with-parent',
            '--new-session', '--chdir', str(PRIVATE), '--clearenv']
    if linux_runtime:
        import linux_runtime as linux
        # pressure-vessel needs a writable reference lock even for read-only
        # inputs. Overlay only a new private empty file, never the source lock.
        (output/'runtime.ref').touch()
        args += ['--bind', str(output/'runtime.ref'), str(linux.platform(linux_runtime.resolve())/'files/.ref')]
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(PRIVATE/'home'),
           'XDG_CONFIG_HOME': str(PRIVATE/'config'), 'XDG_RUNTIME_DIR': str(PRIVATE/'run'),
           'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE',
           'QT_QPA_PLATFORM': 'offscreen', 'KWIN_COMPOSE': 'Q', 'LIBGL_ALWAYS_SOFTWARE': '1',
           '__EGL_VENDOR_LIBRARY_FILENAMES': '/usr/share/glvnd/egl_vendor.d/50_mesa.json',
           '__GLX_VENDOR_LIBRARY_NAME': 'mesa', 'GALLIUM_DRIVER': 'llvmpipe',
           'PENGUIN_PRIVATE_PROFILE_PROBE': '1', 'QT_NO_XDG_DESKTOP_PORTAL': '1',
           'QT_FORCE_STDERR_LOGGING': '1', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    if protected:
        env['LD_PRELOAD'] = str(PRIVATE/'protected-compositor.so')
    for key, value in env.items():
        args += ['--setenv', key, value]
    args += ['dbus-run-session', '--', 'kwin_wayland_wrapper', '--virtual', '--xwayland',
             '--no-lockscreen', '--no-global-shortcuts', '--no-kactivities',
             '--exit-with-session', str(PRIVATE/'session')]
    (output/'command.json').write_text(json.dumps(args, indent=2)+'\n')
    with (output/'kwin.log').open('w') as log:
        subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=55)
    result = json.loads((output/'result.json').read_text())
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--linux-runtime', type=Path, help='Also test an existing private Linux runtime')
    parser.add_argument('--production-observer', action='store_true', help='Also exercise the production focus backend without G HUB')
    parser.add_argument('--protected-compositor', action='store_true',
                        help='Reproduce denied KWin procfs access inside the private test session')
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.inside and not args.output:
        parser.error('--output is required')
    if args.protected_compositor and not args.production_observer:
        parser.error('--protected-compositor requires --production-observer')
    if args.inside:
        inside()
        # KWin's wrapper retries a nonzero session exit, overwriting evidence.
        # The outer process reads result.json and propagates the actual failure.
        sys.exit(0)
    sys.exit(run(args.output, args.linux_runtime, args.production_observer, args.protected_compositor))
