#!/usr/bin/env python3
"""Create a separate, version-checked G HUB archive with local color preview.

Does not modify the input installation. The private Wine runner can mount the
result for a new session while keeping the installed vendor archive unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


BUNDLE_SHA256 = 'ae6f018dc654c415afb6e7e7611335692df89dd3cf99bf3a952d82a811589673'
REPLACEMENTS = (
    ('return x3({},N3(e.color,t.oldHue))',
     'return e.ghubLocalPreview&&e.color===t.__ghubParentColor?null:'
     'x3({},N3(e.color,t.oldHue),e.ghubLocalPreview?{__ghubParentColor:e.color}:{})'),
    ('(0,t.jsx)(k5,{style:s,hidePickerContainer:this.props.hidePickerContainer,color:i.asHexString(),',
     '(0,t.jsx)(k5,{ghubLocalPreview:!0,style:s,hidePickerContainer:this.props.hidePickerContainer,color:i.asHexString(),'),
)


def entries(tree, parent=''):
    for name, item in tree['files'].items():
        path = parent + name
        if 'files' in item:
            yield from entries(item, path + '/')
        elif 'offset' in item:
            yield path, item


def patch_bundle(bundle):
    if hashlib.sha256(bundle).hexdigest() != BUNDLE_SHA256:
        raise ValueError('Unrecognized G HUB bundle; identify the picker again before patching')
    text = bundle.decode('utf-8')
    for old, new in REPLACEMENTS:
        if text.count(old) != 1:
            raise ValueError('Picker patch does not have exactly one matching site')
        text = text.replace(old, new)
    return text.encode('utf-8')


def prepare(source, output):
    original = source.read_bytes()
    _, size, _, length = struct.unpack('<4I', original[:16])
    header = json.loads(original[16:16 + length])
    payload = original[8 + size:]
    item = header['files']['app.min.js']
    start, length = int(item['offset']), item['size']
    old_bundle = payload[start:start + length]
    changed = patch_bundle(old_bundle)
    delta = len(changed) - length
    old_entries = {name:hashlib.sha256(payload[int(e['offset']):int(e['offset']) + e['size']]).hexdigest()
                   for name, e in entries(header)}
    for name, entry in entries(header):
        offset = int(entry['offset'])
        if name != 'app.min.js' and offset < start + length and offset + entry['size'] > start:
            raise ValueError('Overlapping ASAR entries')
        if offset >= start + length:
            entry['offset'] = str(offset + delta)
    item['size'] = len(changed)
    integrity = item.get('integrity')
    if integrity:
        if integrity['algorithm'] != 'SHA256' or integrity['blockSize'] <= 0:
            raise ValueError('Unsupported archive integrity format')
        integrity['hash'] = hashlib.sha256(changed).hexdigest()
        block = integrity['blockSize']
        integrity['blocks'] = [hashlib.sha256(changed[i:i + block]).hexdigest()
                               for i in range(0, len(changed), block)]
    new_payload = payload[:start] + changed + payload[start + length:]
    for name, entry in entries(header):
        data = new_payload[int(entry['offset']):int(entry['offset']) + entry['size']]
        expected = hashlib.sha256(changed).hexdigest() if name == 'app.min.js' else old_entries[name]
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Unexpected archive content change: ' + name)
    encoded = json.dumps(header, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    padded = (len(encoded) + 3) & ~3
    result = (struct.pack('<4I', 4, 8 + padded, 4 + padded, len(encoded)) + encoded +
              bytes(padded - len(encoded)) + new_payload)
    with output.open('xb') as f:
        f.write(result)
    return {'source':str(source.resolve()), 'source_sha256':hashlib.sha256(original).hexdigest(),
            'output':str(output.resolve()), 'output_sha256':hashlib.sha256(result).hexdigest(),
            'bundle_before_sha256':BUNDLE_SHA256, 'bundle_after_sha256':hashlib.sha256(changed).hexdigest(),
            'packed_entries_verified':len(old_entries), 'changed_entries':['app.min.js'],
            'scope':'Retain local picker state for unchanged parent color; original throttle and deferred device callback retained.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = prepare(args.source, args.output)
    report_path = args.output.with_suffix('.provenance.json')
    with report_path.open('x') as f:
        json.dump(report, f, indent=2)
        f.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
