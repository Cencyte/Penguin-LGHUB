#!/usr/bin/env python3
"""Move stopped Linux x86-64 processes' regular-file backing, retaining their RAM state.

Requires an explicit process/start-time allowlist and source-directory aliases.
Preserves shared open-file descriptions, file positions, flags, and POSIX locks.
Refuses memory mappings and deleted files. Original files and descriptor backups
are retained until all participants have been verified. No process is restarted.
"""
import argparse
import array
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time

LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.syscall.restype = ctypes.c_long


def native(number, *args):
    result = LIBC.syscall(ctypes.c_long(number), *[ctypes.c_long(a) for a in args])
    if result == -1:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def identity(pid):
    path = Path('/proc') / str(pid)
    return dict(start_tick=(path / 'stat').read_text().rsplit(')', 1)[1].split()[19],
                command_sha256=hashlib.sha256((path / 'cmdline').read_bytes()).hexdigest())


def stopped(pid):
    for task in (Path('/proc') / str(pid) / 'task').iterdir():
        if (task / 'stat').read_text().rsplit(')', 1)[1].split()[0] not in ('T', 't'):
            return False
    return True


def wait_stopped(pid):
    deadline = time.monotonic() + 15
    while not stopped(pid):
        if time.monotonic() > deadline:
            raise RuntimeError(f'Process {pid} did not fully stop')
        time.sleep(.01)


def fd_info(pid, fd):
    data = (Path('/proc') / str(pid) / 'fdinfo' / str(fd)).read_text()
    fields = dict(line.split(':', 1) for line in data.splitlines() if not line.startswith('lock:'))
    locks = []
    for line in data.splitlines():
        if not line.startswith('lock:'):
            continue
        parts = line.split()
        if parts[2:4] != ['POSIX', 'ADVISORY'] or parts[4] not in ('READ', 'WRITE') or int(parts[5]) != pid:
            raise RuntimeError('Only process-owned POSIX advisory locks are supported: ' + line)
        start = int(parts[7]); end = parts[8]
        locks.append(dict(type=parts[4], start=start, length=0 if end == 'EOF' else int(end) - start + 1))
    return dict(pos=int(fields['pos']), flags=int(fields['flags'].strip(), 8), locks=locks)


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_state(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def redirect(pid, entries, descriptors, config, work, label):
    uid = (Path('/proc') / str(pid)).stat().st_uid
    with tempfile.TemporaryDirectory(prefix='dragonshard-fd-', dir='/tmp') as tmp:
        directory = Path(tmp); os.chown(directory, uid, -1)
        address = directory / 'fd.sock'
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        listener.bind(str(address)); os.chown(address, uid, -1); os.chmod(address, 0o600)
        listener.listen(1); listener.settimeout(30)
        errors = []

        def send():
            try:
                connection, _ = listener.accept()
                with connection:
                    peer, _, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if peer != pid:
                        raise RuntimeError('Unexpected descriptor recipient')
                    for entry in entries:
                        connection.sendmsg([b'F'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                            array.array('i', [descriptors[entry['group']]]))])
            except BaseException as error:
                errors.append(str(error))

        server = threading.Thread(target=send, daemon=True); server.start()
        plan = work / f'{label}-{pid}.json'; result = work / f'{label}-{pid}-result.json'
        write_state(plan, dict(pid=pid, socket=str(address), fds=entries, result=str(result)))
        runtime = Path(config['debugger_runtime'])
        env = dict(os.environ, LD_LIBRARY_PATH=str(runtime / 'usr/lib'), PYTHONHOME=str(runtime / 'usr'),
                   DEBUGINFOD_URLS='', DRAGONSHARD_FD_PLAN=str(plan))
        helper = Path(__file__).with_name('live_fd_gdb.py')
        with (work / f'{label}-{pid}.log').open('w') as log:
            run = subprocess.run(['/usr/bin/gdb', '--nx', '--batch', '-ex',
                'python exec(open(' + repr(str(helper)) + ').read())'], env=env, stdout=log, stderr=subprocess.STDOUT)
        listener.close(); server.join(timeout=31)
        wait_stopped(pid)
        report = json.loads(result.read_text()) if result.exists() else {}
        if run.returncode or errors or not report.get('complete'):
            raise RuntimeError(f'Descriptor redirection failed for {pid}: {errors} {report}')


def move(config, work):
    if os.geteuid() != 0 or os.uname().machine != 'x86_64':
        raise RuntimeError('This operation requires local root on Linux x86-64')
    work.mkdir(parents=True, exist_ok=False)
    pids = [int(pid) for pid in config['processes']]
    roots = [(Path(row['source']), Path(row['destination'])) for row in config['directories']]
    for source, target in roots:
        if source.is_symlink() or not source.is_dir() or not target.is_symlink() or target.resolve() != source.resolve():
            raise RuntimeError('Expected a real source and its destination alias')
    for pid in pids:
        if identity(pid) != config['processes'][str(pid)]:
            raise RuntimeError('A process identity changed')
        for line in (Path('/proc') / str(pid) / 'maps').read_text().splitlines():
            if any(str(source) in line or str(target) in line for source, target in roots):
                raise RuntimeError('A file is memory-mapped; descriptor substitution is insufficient')
    total = sum(p.stat().st_size for source, _ in roots for p in source.rglob('*') if p.is_file() and not p.is_symlink())
    if shutil.disk_usage(roots[0][1].parent).free < total + config.get('reserve_bytes', 22 * 1024**3):
        raise RuntimeError('Destination does not fit the live files and disk reserve')
    original, replacement, groups, entries, publications = [], [], [], {}, []
    state = dict(state='stopping_owned_processes', pids=pids, copied_bytes=0, descriptors=0)
    write_state(work / 'status.json', state)
    redirected = False
    try:
        for pid in pids: os.kill(pid, signal.SIGSTOP)
        for pid in pids: wait_stopped(pid)
        # Recheck mappings after every application thread has stopped.
        for pid in pids:
            for line in (Path('/proc') / str(pid) / 'maps').read_text().splitlines():
                if any(str(source) in line or str(target) in line for source, target in roots):
                    raise RuntimeError('A mapping appeared before the freeze')
            entries[pid] = []
            pidfd = os.pidfd_open(pid)
            try:
                for fd in sorted((Path('/proc') / str(pid) / 'fd').iterdir(), key=lambda p: int(p.name)):
                    path = Path(os.readlink(fd))
                    mapping = next(((source, target) for source, target in roots if path.is_relative_to(source)), None)
                    if not mapping:
                        continue
                    if not path.exists() or not stat.S_ISREG(fd.stat().st_mode):
                        raise RuntimeError('Cannot migrate deleted or nonregular open files')
                    info = fd_info(pid, int(fd.name))
                    group = next((i for i, old in enumerate(original)
                        if native(312, os.getpid(), pid, 0, old, int(fd.name)) == 0), None)
                    if group is None:
                        group = len(original)
                        original.append(native(438, pidfd, int(fd.name), 0))
                        groups.append(dict(path=str(path), pos=info['pos'], flags=info['flags']))
                    entries[pid].append(dict(fd=int(fd.name), group=group, **info))
            finally:
                os.close(pidfd)
        state.update(state='copying_frozen_backing_files', descriptors=sum(map(len, entries.values())), groups=len(groups))
        write_state(work / 'status.json', state)
        for source, target in roots:
            staging = target.with_name(target.name + '.live-fd-staging')
            backup = source.with_name(source.name + '.before-live-fd-move')
            if staging.exists() or backup.exists():
                raise RuntimeError('Staging or backup path already exists')
            shutil.copytree(source, staging, symlinks=True)
            for path in [source, *source.rglob('*')]:
                copied = staging / path.relative_to(source)
                info = path.lstat(); os.chown(copied, info.st_uid, info.st_gid, follow_symlinks=False)
                if path.is_file() and not path.is_symlink():
                    if digest(path) != digest(copied):
                        raise RuntimeError('Frozen file checksum mismatch: ' + str(path))
                    with copied.open('rb') as stream: os.fsync(stream.fileno())
                    state['copied_bytes'] += info.st_size
            # All participants remain stopped throughout publication and fd swaps.
            target.unlink(); staging.rename(target)
            source.rename(backup); source.symlink_to(target, target_is_directory=True)
            publications.append((source, target, backup))
            write_state(work / 'status.json', state)
        for group in groups:
            # Published source aliases now resolve to the verified ZFS copies.
            flags = group['flags'] & ~(os.O_CLOEXEC | os.O_CREAT | os.O_EXCL | os.O_TRUNC)
            fd = os.open(group['path'], flags)
            os.lseek(fd, group['pos'], os.SEEK_SET)
            replacement.append(fd)
        state['state'] = 'redirecting_descriptors'; write_state(work / 'status.json', state)
        redirected = True
        for pid in pids:
            if entries[pid]: redirect(pid, entries[pid], replacement, config, work, 'forward')
        for pid in pids:
            for entry in entries[pid]:
                info = fd_info(pid, entry['fd'])
                if any(info[key] != entry[key] for key in ('pos', 'flags', 'locks')):
                    raise RuntimeError(f'Position/flags/lock verification failed for {pid}/{entry["fd"]}: {info} != {entry}')
                if native(312, os.getpid(), pid, 0, replacement[entry['group']], entry['fd']) != 0:
                    raise RuntimeError('Shared open-file description was not preserved')
                if os.stat(f'/proc/{pid}/fd/{entry["fd"]}').st_dev != roots[0][1].stat().st_dev:
                    raise RuntimeError('Descriptor is not on the destination filesystem')
        state.update(state='complete', all_descriptors_verified=True,
                     preserved_backups=[str(row[2]) for row in publications])
        write_state(work / 'status.json', state)
    except BaseException as error:
        state.update(state='rolling_back', error=str(error)); write_state(work / 'status.json', state)
        try:
            if redirected:
                for pid in pids:
                    if entries[pid]: redirect(pid, entries[pid], original, config, work, 'rollback')
            for source, target, backup in reversed(publications):
                source.unlink(); backup.rename(source)
                target.rename(target.with_name(target.name + '.failed-live-fd-copy'))
                target.symlink_to(source, target_is_directory=True)
            state['state'] = 'rolled_back'
        except BaseException as rollback_error:
            state.update(state='rollback_requires_attention', rollback_error=str(rollback_error))
        write_state(work / 'status.json', state)
        raise
    finally:
        for fd in original + replacement: os.close(fd)
        # The caller controls resumption after reviewing the verified result.
        # Never silently resume a partially redirected application.


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path); parser.add_argument('work', type=Path)
    args = parser.parse_args()
    move(json.loads(args.config.read_text()), args.work)
