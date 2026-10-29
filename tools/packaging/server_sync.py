#!/usr/bin/env python3
"""Keep the pinned Wine runtime on its tested wineserver synchronization path.

Wine 10.20 automatically opens /dev/ntsync when available. That path breaks
the packaged CoreMessaging registrar's wait lifecycle on newer kernels.
Mask only that device inside the application namespace, using an anonymous,
unreadable file. Host device permissions and other Wine sessions are unchanged.
"""
import errno
import os
from pathlib import Path
import sys


def boundary_command(command):
    """Return the inner bwrap command and an optional FD owned by the caller."""
    if '--' not in command or '--dev-bind' not in command:
        raise ValueError('Expected the application bwrap boundary')
    command = list(command)
    if not Path('/dev/ntsync').exists():
        return command, None
    fd = os.memfd_create('penguin-server-sync', os.MFD_CLOEXEC)
    # bwrap copies this empty FD into its own mount, then applies mode 000.
    # /dev/null is unsuitable: open() would succeed and Wine would attempt
    # unsupported NTSYNC ioctls instead of selecting server synchronization.
    i = command.index('--')
    command[i:i] = ['--perms', '000', '--ro-bind-data', str(fd), '/dev/ntsync']
    return command, fd


def verify_server_sync():
    try:
        fd = os.open('/dev/ntsync', os.O_RDONLY | os.O_CLOEXEC)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.EACCES, errno.EPERM):
            return
        raise
    else:
        os.close(fd)
        raise RuntimeError('NTSYNC is accessible; restart through the packaged synchronization boundary')


def main():
    if os.geteuid() == 0:
        raise RuntimeError('Run the G HUB desktop as a normal user')
    command, fd = boundary_command(sys.argv[1:])
    try:
        if fd is not None:
            os.set_inheritable(fd, True)
        print('Penguin LGHUB: using Wine server synchronization', file=sys.stderr, flush=True)
        os.execv(command[0], command)
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == '__main__':
    main()
