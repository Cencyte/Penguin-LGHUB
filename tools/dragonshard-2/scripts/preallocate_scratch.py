#!/usr/bin/env python3
"""Reserve owned Ghidra scratch extents without changing file contents or logical lengths."""
import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import time

GIB = 1024**3
_libc = ctypes.CDLL(None, use_errno=True)
_fallocate = _libc.fallocate
_fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_longlong, ctypes.c_longlong]
_fallocate.restype = ctypes.c_int


def reserve_file(path, reserve_bytes):
    # Open the existing inode without following links. A worker may unlink its cache
    # concurrently; never recreate that pathname or truncate a live database.
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError('Scratch path is not a regular file')
        target = ((before.st_size + GIB // 4 + GIB - 1) // GIB) * GIB
        extra = max(0, target - before.st_blocks * 512)
        if not extra or shutil.disk_usage(path.parent).free < extra + reserve_bytes:
            return 0
        # FALLOC_FL_KEEP_SIZE reserves physical extents; bytes and EOF are untouched.
        if _fallocate(fd, 1, 0, target):
            error = ctypes.get_errno()
            if error in (errno.EOPNOTSUPP, errno.ENOSYS, errno.ENOSPC):
                return 0
            raise OSError(error, os.strerror(error), str(path))
        return extra
    finally:
        os.close(fd)


def reserve_caches(root, reserve_bytes):
    total = 0
    for path in root.glob('round-*/workers/*/temp/*/ghidra*.cache'):
        if not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError('Scratch file escaped the owned analysis directory')
        try:
            total += reserve_file(path, reserve_bytes)
        except FileNotFoundError:
            pass  # Worker completed and released its private cache.
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    if json.loads((root / 'owner.json').read_text()).get('tool') != 'dragonshard-2-parallel-analysis':
        raise RuntimeError('Not an owned parallel-analysis directory')
    while True:
        status = json.loads((root / 'status.json').read_text())
        if status['state'] in ('complete', 'blocked_or_failed'):
            return
        packing = json.loads((root / 'packing.json').read_text())
        protected = 16 * GIB + packing.get('pending_output_bytes', 0) + packing.get('merge_scratch_bytes', 0)
        reserved = reserve_caches(root, protected)
        if reserved:
            print(json.dumps(dict(at=time.time(), preallocated_bytes=reserved)), flush=True)
        time.sleep(5)


if __name__ == '__main__':
    main()
