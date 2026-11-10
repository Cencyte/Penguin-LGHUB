"""Rebuild factual model/USB/depot metadata from verified official resources.

Developer tool only. Does not publish decryption keys or vendor resource files.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from logitech_capsule import decrypt, public_keys
from windows_media_acquire import ROOT


def identities(value):
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'id' and isinstance(item, str):
                match = re.search(r'(?:^|_)([0-9a-f]{4})_([0-9a-f]{4})(?:_|$)', item, re.I)
                if match:
                    result.add(':'.join(match.groups()).lower())
            elif isinstance(item, (dict, list)):
                result.update(identities(item))
    elif isinstance(value, list):
        for item in value:
            result.update(identities(item))
    return result


def model_record(model, lock):
    depots = set()
    for key in ('depot', 'dfuDepot'):
        if model.get(key):
            depots.add(model[key])
    depots.update(model.get('depots', {}).values())
    if model.get('depotPrefix'):
        depots.update(name for name in lock['depots'] if name.startswith(model['depotPrefix']))
    capabilities = model.get('capabilities', {})
    return {'model': model['modelId'],
            'name': model.get('extendedDisplayName', model.get('displayName', model['modelId'])),
            'type': model['type'], 'identities': sorted(identities(model.get('modes', []))),
            'depots': sorted(depots), 'subdevice': 'subdevicesTypes' in capabilities,
            'non_interactive': bool(capabilities.get('nonInteractive'))}


def build(stage, cache):
    lock = json.loads((ROOT / 'packaging/logitech-depots.lock.json').read_text())
    report = json.loads((stage / 'extraction.json').read_text())
    keys = public_keys(cache, lock['public_access'])
    devices, missing, sources = [], [], []
    records = {r['path']: r for r in report['extracted']['core']}
    for path in sorted((stage / 'depots/core/data/devices').glob('*.json')):
        name = str(path.relative_to(stage / 'depots/core'))
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if records[name]['sha256'] != digest:
            raise ValueError('Device catalog hash changed: ' + name)
        sources.append({'path': name, 'sha256': digest})
        if data[:4] == struct.pack('<I', 0x20210521):
            size = struct.unpack_from('<I', data, 4)[0]
            header = json.loads(data[8:8+size])
            if header['key-id'] not in keys:
                missing.append(name)
                continue
            data = decrypt(data[12+size:], header['file-sha'], header['key-id'], keys)
            if hashlib.sha256(data).hexdigest() != header['file-sha']:
                raise ValueError('Decoded device catalog hash changed')
        devices.extend(model_record(m, lock) for m in json.loads(data).get('devices', []))
    if not devices or not sources:
        raise ValueError('No verified device catalog found')
    return {'schema': 1, 'build_id': lock['build_id'],
            'basis': 'Identity/resource metadata derived from the hash-verified pinned official device catalog. No hardware qualification is inferred.',
            'unreadable_catalog_sections': len(missing), 'unavailable_sections': missing,
            'sources': sources, 'devices': sorted(devices, key=lambda d: d['model'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(build(args.stage, args.cache), indent=2) + '\n')
