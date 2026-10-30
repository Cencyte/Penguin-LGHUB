"""Journalled install/commit/rollback, with diagnostics outside the payload."""
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import signal
import traceback

import installation_state as ownership
import uninstall


class InstallationInterrupted(KeyboardInterrupt):
    pass


@contextmanager
def catch_termination():
    def abort(signum, frame):
        raise InstallationInterrupted('Installation interrupted by signal ' + str(signum))
    previous = {s: signal.signal(s, abort) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@contextmanager
def finish_rollback():
    # Repeated Ctrl-C must not abandon cleanup halfway through. SIGKILL and power
    # loss cannot run handlers; the durable receipt permits recovery next run.
    previous = {s: signal.signal(s, signal.SIG_IGN) for s in
                (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@contextmanager
def target_lock(root, diagnostic_dir):
    # Outside the removable tree: serializes recovery plus creation of a new
    # transaction at the same path, including the interval after rollback.
    key = hashlib.sha256(os.fsencode(root)).hexdigest()
    path = diagnostic_dir / ('transaction-' + key + '.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another installer is using this target') from exc
        yield


def archive_diagnostics(root, state, ui):
    destination = ui.log_path.parent / ('failed-' + state['installation_id'])
    destination.mkdir(exist_ok=True, mode=0o700)
    # Old previews stored their central logs inside the payload. Preserve those
    # before rolling back; new transaction logs are already outside it.
    logdir = root / 'logs'
    if logdir.is_dir() and not logdir.is_symlink():
        for path in logdir.iterdir():
            if path.is_file() and not path.is_symlink() and path.suffix in ('.log', '.json'):
                target = destination / path.name
                if not target.exists():
                    shutil.copyfile(path, target);target.chmod(0o600)
    ownership.save(destination, state)
    return destination


class CleanupLog:
    """Best-effort diagnostics when a full disk is itself the failure cause."""
    def __init__(self, stream):self.stream=stream
    def write(self, data):
        try:return self.stream.write(data)
        except OSError:return len(data)
    def flush(self):
        try:self.stream.flush()
        except OSError:pass


def rollback(root, state, ui):
    with finish_rollback():
        original_log=ui.log;ui.log=CleanupLog(original_log)
        destination=None
        try:
            state['transaction'] = 'rolling-back'
            try:
                ownership.save(root, state)
                destination = archive_diagnostics(root, state, ui)
            except OSError as exc:
                ui.write('Could not save all rollback diagnostics: '+str(exc))
            ui.step('System', 'Roll back the incomplete installation')
            try:
                uninstall.remove_owned(root, state, ui, rollback=True)
            except BaseException as exc:
                traceback.print_exc(file=ui.log);ui.log.flush()
                state['transaction'] = 'rollback-failed';state['rollback_error'] = str(exc)
                try:
                    if root.exists():ownership.save(root, state)
                    if destination:ownership.save(destination, state)
                except OSError:pass
                raise RuntimeError('Rollback is incomplete: ' + str(exc) +
                                   '. Retry --install to recover; diagnostics: ' + str(ui.log_path.parent)) from exc
            state['transaction'] = 'rolled-back'
            if destination:
                try:ownership.save(destination, state)
                except OSError:ui.write('Payload removed; the final diagnostic record could not be updated.')
            ui.write('Rollback complete. Only available diagnostics were retained.')
        finally:
            ui.log=original_log


def synchronize(root, state):
    """Report filesystem writeback errors before marking the payload committed.

    Linux syncfs flushes the filesystem containing each managed path, including
    files written by earlier build helpers. A receipt fsync alone does not make
    those payload writes durable. Unit tests replace this host I/O boundary.
    """
    syncfs=ctypes.CDLL(None,use_errno=True).syncfs
    syncfs.argtypes=[ctypes.c_int];syncfs.restype=ctypes.c_int
    seen=set()
    for path in (root, *(Path(name).parent for name in state['launchers'])):
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            device=os.fstat(fd).st_dev
            if device in seen:continue
            if syncfs(fd)!=0:
                code=ctypes.get_errno()
                raise OSError(code,os.strerror(code),str(path))
            seen.add(device)
        finally:os.close(fd)


def execute(directory, source, version, ui, operation):
    root = ownership.checked_directory(directory)
    if ui.log_path.resolve().is_relative_to(root):
        raise ValueError('Transaction diagnostics must be outside the installation directory')
    with target_lock(root, ui.log_path.parent), catch_termination():
        if root.exists() and any(root.iterdir()):
            with ownership.managed_directory(root) as (_, state):
                if state.get('installed'):
                    raise ValueError('An installation is already committed here; it is unchanged. '
                                     'Use --uninstall explicitly before replacing it.')
                ui.step('System', 'Recover a previous incomplete installation')
                rollback(root, state, ui)
        with ownership.managed_directory(root, create=True, source=source, version=version) as (_, state):
            committed = False
            try:
                state['transaction'] = 'preparing'
                ownership.save(root, state)
                operation(root, state)
                # Reload launchers recorded by child helpers before committing.
                state = ownership.load(root)
                uninstall.launcher_paths(state)
                if any(not Path(path).is_file() for path in state['launchers']):
                    raise RuntimeError('A registered launcher is missing before commit')
                ui.step('Verify', 'Flush installation files before committing')
                synchronize(root,state)
                ui.step('Verify', 'Commit the verified installation')
                state['installed'] = True
                state['transaction'] = 'committed'
                state['owner_acceptance_pending'] = True
                ownership.save(root, state)
                committed = True
            except BaseException:
                try:
                    traceback.print_exc(file=ui.log);ui.log.flush()
                except OSError:pass
                # Never undo a durable commit merely because final reporting
                # failed or a signal arrived immediately after the commit.
                latest = ownership.load(root)
                if not committed:
                    latest['installed'] = False
                    rollback(root, latest, ui)
                raise
