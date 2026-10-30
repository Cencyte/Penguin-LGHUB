"""Remove one receipt-owned installation without probing a desktop or distro."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

import installation_state
from installer_ui import Presentation, ROOT


def mounted_paths():
    # Includes bind mounts on the same device; st_dev alone cannot detect them.
    return [Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
            for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def check_tree(root):
    for name in ('desktop', 'work', 'source', 'logs', 'toolchain', 'linux-runtime', 'helpers'):
        if (root / name).is_symlink():
            raise RuntimeError('Managed top-level directory is now a symlink: ' + str(root / name))
    for mount in mounted_paths():
        if mount == root or mount.is_relative_to(root):
            raise RuntimeError('Unmount this installation path before removal: ' + str(mount))


def launcher_paths(state):
    paths = []
    for name, expected in state['launchers'].items():
        path = Path(name)
        if not path.is_absolute() or path.suffix != '.desktop' or not path.name.startswith('Penguin-LGHUB'):
            raise ValueError('Invalid launcher ownership record: ' + name)
        if path.is_symlink() or path.parent.resolve() != path.parent:
            raise RuntimeError('Launcher is now a symlink; preserve and review it: ' + name)
        if not path.exists():
            continue
        if (not path.is_file() or path.stat().st_uid != os.getuid()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise RuntimeError('Launcher has changed; move it aside to keep it before retrying: ' + name)
        paths.append(path)
    return paths


def remove_payload(root):
    # Keep the receipt until the bulky/removable contents are gone. If cleanup
    # fails or is interrupted, the same command can still validate and resume.
    control = {'installation.json', '.install.lock', '.uninstalling'}
    for child in root.iterdir():
        if child.name in control:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for name in ('.uninstalling', '.install.lock', 'installation.json'):
        (root / name).unlink(missing_ok=True)
    root.rmdir()


@contextmanager
def stopped_session(root, ui, timeout=30):
    desktop_root = root / 'desktop'
    if not desktop_root.exists():
        yield
        return
    import desktop_runtime
    desktop = desktop_runtime.desktop
    previous = desktop.SCRIPT
    desktop.SCRIPT = root / 'source/tools/packaging/desktop_runtime.py'
    guard = None
    try:
        config = desktop_root / 'desktop.json'
        state = desktop_root / 'session.json'
        ui.step('Desktop', 'Stop this installation’s verified G HUB session')
        desktop.stop(config, state)
        fd = os.open(desktop_root / 'launch.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        guard = os.fdopen(fd, 'w')
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('The launcher is still active; quit its session and retry removal')
                time.sleep(.1)
        if desktop.session_owner(config, state) or any(desktop.prefix_processes(prefix) for prefix in
                (desktop_root / 'prefix', root / 'work/candidate/prefix')):
            raise RuntimeError('Processes still use this installation; quit that session and retry removal')
        yield
    finally:
        if guard:
            guard.close()
        desktop.SCRIPT = previous


def remove(directory, ui):
    with installation_state.managed_directory(directory) as (root, state):
        ui.step('Verify', 'Verify installation ownership, launchers and mount boundaries')
        check_tree(root)
        launcher_paths(state)
        marker = root / '.uninstalling'
        if marker.is_symlink():
            raise ValueError('Symlinked removal marker')
        marker.touch(mode=0o600)
        try:
            with stopped_session(root, ui):
                if state['attempts'].get('rules'):
                    ui.step('System', 'Remove this installation’s Logitech device rule')
                    ui.run(['sudo', sys.executable, str(ROOT / 'tools/packaging/install_device_rules.py'),
                            '--installation-id', state['installation_id'], '--remove'])
                ui.step('Desktop', 'Remove recorded desktop and application-menu launchers')
                # Recheck after sudo/user interaction before deleting any external file.
                for path in launcher_paths(state):
                    path.unlink()
                ui.step('G HUB', 'Remove the private runtime, settings, downloads and installation logs')
                check_tree(root)
                # Linux's fd-based rmtree does not follow Wine's user-folder symlinks.
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise RuntimeError('This Python cannot safely remove a tree containing symlinks')
                remove_payload(root)
        finally:
            if marker.exists():
                marker.unlink()
    ui.write('Penguin-LGHUB removed. The source checkout and unrelated software are unchanged.')


def run(directory, verbose=False):
    # A successful removal leaves no new permanent uninstall log. Failed removal
    # retains diagnostics beside independent --check/project-test reports.
    with tempfile.TemporaryDirectory(prefix='penguin-lghub-uninstall-') as tmp:
        ui = Presentation(Path(tmp) / 'uninstall.log', verbose)
        try:
            ui.header()
            ui.write('This removes G HUB settings and all data in the managed installation directory.')
            root = installation_state.checked_directory(directory)
            if not root.exists():
                ui.step('Verify', 'Check installation location')
                ui.write('No installation exists at ' + str(root))
                return 0
            remove(root, ui)
            return 0
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            traceback.print_exc(file=ui.log);ui.log.flush()
            state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'penguin-lghub'
            state.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Exclusive creation avoids overwriting an earlier failure report.
            fd, name = tempfile.mkstemp(prefix='uninstall-failed-', suffix='.log', dir=state)
            with os.fdopen(fd, 'wb') as out:
                out.write(ui.log_path.read_bytes())
            ui.write('Removal stopped: ' + str(error))
            ui.write('Details: ' + name)
            return 130 if isinstance(error, KeyboardInterrupt) else 1
        finally:
            ui.close()
