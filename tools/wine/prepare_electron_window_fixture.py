#!/usr/bin/env python3
"""Copy the installed Electron runtime and package a deterministic window fixture.

No downloads, npm install, or changes to the source G HUB installation.
"""
import argparse
import json
from pathlib import Path
import struct
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source = args.app_dir.resolve(strict=True)
    if not (source / 'lghub.exe').is_file():
        parser.error('Supply the installed LGHUB application directory')
    args.output.mkdir(parents=True, exist_ok=False)
    for file in source.iterdir():
        if file.name == 'lghub.exe' or file.suffix.lower() in ('.dll', '.pak', '.bin', '.dat') or file.name == 'locales':
            subprocess.run(['cp', '-a', '--reflink=auto', str(file), str(args.output / file.name)], check=True)
    # ASAR's header is a Chromium pickle: length pickle, then string pickle.
    resources = args.output / 'resources'
    resources.mkdir()
    contents = {
        'package.json': b'{"name":"ghub-window-regression","version":"1.0.0","main":"main.js"}',
        'main.js': (Path(__file__).parent / 'electron_window_fixture/main.js').read_bytes(),
    }
    files, offset = {}, 0
    for name, data in contents.items():
        files[name] = {'size':len(data), 'offset':str(offset)}
        offset += len(data)
    header = json.dumps({'files':files}, separators=(',', ':')).encode()
    size = (len(header) + 3) & ~3
    archive = struct.pack('<4I', 4, 8 + size, 4 + size, len(header)) + header + bytes(size - len(header))
    (resources / 'app.asar').write_bytes(archive + b''.join(contents.values()))
    print(args.output.resolve())


if __name__ == '__main__':
    main()
