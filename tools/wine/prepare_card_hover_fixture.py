#!/usr/bin/env python3
"""Package G HUB's original card CSS and Electron into an isolated hover fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    archive = args.app_dir / 'resources/app.asar'
    with archive.open('rb') as f:
        _, size, _, length = struct.unpack('<4I', f.read(16))
        header = json.loads(f.read(length))
        entry = header['files']['app.min.css']
        f.seek(8 + size + int(entry['offset']))
        css = f.read(entry['size'])
    required = [b'.dls-Card:not([data-disabled=true]):hover',
                b'.GhubHome .DeviceList .animated-card-container', b'.DeviceCard']
    if not all(selector in css for selector in required):
        parser.error('Reidentify the installed card hierarchy and CSS for this version')
    args.output.mkdir(parents=True, exist_ok=False)
    for file in args.app_dir.iterdir():
        if file.name == 'lghub.exe' or file.suffix.lower() in ('.dll', '.pak', '.bin', '.dat') or file.name == 'locales':
            subprocess.run(['cp', '-a', '--reflink=auto', str(file), str(args.output / file.name)], check=True)
    contents = {'package.json':b'{"name":"ghub-hover-regression","version":"1.0.0","main":"main.js"}',
                'app.min.css':css}
    for name in ('main.js', 'index.html'):
        contents[name] = (Path(__file__).parent / 'card_hover_fixture' / name).read_bytes()
    files, offset = {}, 0
    for name, data in contents.items():
        files[name] = {'size':len(data), 'offset':str(offset)}
        offset += len(data)
    encoded = json.dumps({'files':files}, separators=(',', ':')).encode()
    padded = (len(encoded) + 3) & ~3
    (args.output / 'resources').mkdir()
    (args.output / 'resources/app.asar').write_bytes(
        struct.pack('<4I', 4, 8 + padded, 4 + padded, len(encoded)) + encoded +
        bytes(padded - len(encoded)) + b''.join(contents.values()))
    (args.output / 'provenance.json').write_text(json.dumps({
        'source':str(archive.resolve()), 'css_sha256':hashlib.sha256(css).hexdigest(),
        'runtime_sha256':hashlib.sha256((args.output / 'lghub.exe').read_bytes()).hexdigest(),
        'scope':'Original CSS and Electron; reconstructed card hierarchy and inert artwork, no agent/device connection.'
    }, indent=2) + '\n')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
