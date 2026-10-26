#!/usr/bin/env python3
"""Prepare a PRIVATE Wine 10.20 DLL probe; never modify the input DLL.

Source equivalent: patches/wine/0001-findfirst-share-delete.patch.
Wine source: https://github.com/wine-mirror/wine/blob/wine-10.20/dlls/kernelbase/file.c
This binary probe is pinned to this workstation's installed DLL SHA-256.
Use only in a disposable/copied prefix during diagnosis. Kernelbase overrides
alone do not select this DLL: use run_private_kernelbase.sh's mount namespace.
"""
import argparse
import hashlib
from pathlib import Path

EXPECTED = '968c38054cdd86f5da69f5325240f9a3db1df360abcb3a02a075ef38b0bddfdf'
OFFSET = 0x1f160
BEFORE = bytes.fromhex('c744242003000000')  # mov dword ptr [rsp+0x20], 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--input', type=Path, default=Path('/usr/lib/wine/x86_64-windows/kernelbase.dll'))
    args = parser.parse_args()
    if args.output.resolve() == args.input.resolve() or args.output.exists():
        parser.error('output must be a new, separate file')
    data = bytearray(args.input.read_bytes())
    if hashlib.sha256(data).hexdigest() != EXPECTED or data[OFFSET:OFFSET+8] != BEFORE:
        parser.error('unrecognized Wine DLL; inspect the installed version before adapting this probe')
    # Fifth NtOpenFile argument: add FILE_SHARE_DELETE (4) to READ|WRITE (3).
    data[OFFSET+4] = 7
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as output:
        output.write(data)
    print(hashlib.sha256(data).hexdigest(), args.output)


if __name__ == '__main__':
    main()
