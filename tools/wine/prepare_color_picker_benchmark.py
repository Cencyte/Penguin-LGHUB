#!/usr/bin/env python3
"""Package the installed G HUB Saturation component into a disposable benchmark.

The vendor code stays in the local lab. No edits to the real application,
downloads, dependency installs, agent connection or peripheral writes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--throttle-ms', type=int, choices=[50, 16], default=50)
    p.add_argument('--controlled-parent', choices=['none', 'delayed', 'preview'], default='none',
                   help='Include original ColorWrap and the native delayed parent, optionally with local preview')
    args = p.parse_args()
    archive = args.app_dir / 'resources/app.asar'
    with archive.open('rb') as f:
        _, size, _, length = struct.unpack('<4I', f.read(16))
        header = json.loads(f.read(length))
        item = header['files']['app.min.js']
        f.seek(8 + size + int(item['offset']))
        source = f.read(item['size']).decode()
    boundary = 'var __webpack_exports__={},_global;'
    throttle = 'n.throttle=(0,s.default)(function(e,t,n){e(t,n)},50)'
    if source.count(boundary) != 1 or source.count(throttle) != 1 or '76659:function' not in source:
        p.error('Unsupported bundle layout; reidentify the component for this version')
    # Export the original webpack require before the application entrypoint.
    # React and Saturation are original bundled modules, without backend sagas.
    vendor = source.split(boundary)[0] + 'window.ghubRequire=__webpack_require__;})();'
    if args.throttle_ms != 50:
        vendor = vendor.replace(throttle, throttle[:-3] + '16)')
    if args.controlled_parent == 'preview':
        original = 'return i({},l.toState(e.color,t.oldHue))'
        replacement = ('return e.ghubLocalPreview&&e.color===t.__ghubParentColor?null:'
                       'i({},l.toState(e.color,t.oldHue),e.ghubLocalPreview?{__ghubParentColor:e.color}:{})')
        if vendor.count(original) != 1:
            p.error('Unsupported ColorWrap layout')
        vendor = vendor.replace(original, replacement)
    args.output.mkdir(parents=True, exist_ok=False)
    for file in args.app_dir.iterdir():
        if file.name == 'lghub.exe' or file.suffix.lower() in ('.dll', '.pak', '.bin', '.dat') or file.name == 'locales':
            subprocess.run(['cp', '-a', '--reflink=auto', str(file), str(args.output / file.name)], check=True)
    app = args.output / 'resources/app'
    app.mkdir(parents=True)
    (app / 'package.json').write_text('{"name":"ghub-color-benchmark","version":"1.0.0","main":"main.js"}')
    (app / 'vendor.js').write_text(vendor)
    for name in ['main.js', 'index.html']:
        (app / name).write_bytes((Path(__file__).parent / 'color_picker_benchmark' / name).read_bytes())
    (app / 'mode.js').write_text('window.pickerParentMode=' + json.dumps(args.controlled_parent) + ';')
    # G HUB enables Electron's OnlyLoadAppFromAsar fuse, so package the fixture
    # exactly as an ASAR instead of depending on a loose resources/app folder.
    payloads = {p.name:p.read_bytes() for p in sorted(app.iterdir())}
    entries, offset = {}, 0
    for name, data in payloads.items():
        entries[name] = {'size':len(data), 'offset':str(offset)}
        offset += len(data)
    header = json.dumps({'files':entries}, separators=(',', ':')).encode()
    padded = (len(header) + 3) & ~3
    (args.output / 'resources/app.asar').write_bytes(
        struct.pack('<4I', 4, 8 + padded, 4 + padded, len(header)) + header +
        bytes(padded - len(header)) + b''.join(payloads.values()))
    (args.output / 'provenance.json').write_text(json.dumps({
        'source':str(archive.resolve()), 'bundle_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'component_module':76659, 'throttle_ms':args.throttle_ms,
        'controlled_parent':args.controlled_parent, 'color_wrap_module':88288,
        'scope':'Original bundled components with parent callback model; excludes full Redux application, agent and peripheral latency.'
    }, indent=2) + '\n')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
