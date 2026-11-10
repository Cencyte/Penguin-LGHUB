"""Read-only USB/HID discovery and pinned device-resource coverage checks.

No HID reports are sent or read. Resource coverage is not hardware acceptance.
"""
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
G_NAME = re.compile(r'\b(?:G\d{2,4}\w*|G502|LIGHTSPEED|GAMING|ASTRO|YETI|BRIO|STREAMCAM|LITRA|POWERPLAY|PRO\s+(?:X|RACING|WIRELESS))\b', re.I)


def read(path):
    try:
        with path.open(errors='replace') as stream:
            return stream.read(4096).strip()
    except OSError:
        return ''


def discover(sysroot=Path('/sys'), devroot=Path('/dev')):
    devices = {}
    for path in sorted((sysroot / 'bus/usb/devices').glob('*')):
        vendor, product = read(path / 'idVendor').lower(), read(path / 'idProduct').lower()
        if not re.fullmatch('[a-f0-9]{4}', vendor) or not re.fullmatch('[a-f0-9]{4}', product):
            continue
        identity = vendor + ':' + product
        devices[(identity, path.name)] = {'id': identity, 'name': read(path / 'product'),
            'bus': 'usb', 'port': path.name, 'driver': (path / 'driver').resolve().name,
            'interfaces': []}
    for path in sorted((sysroot / 'class/hidraw').glob('hidraw*')):
        data = dict(line.split('=', 1) for line in read(path / 'device/uevent').splitlines() if '=' in line)
        fields = data.get('HID_ID', '').split(':')
        if len(fields) != 3:
            continue
        try:
            identity = f'{int(fields[1],16):04x}:{int(fields[2],16):04x}'
        except ValueError:
            continue
        parents = {p.name for p in (path / 'device').resolve().parents}
        matches = [k for k in devices if k[0] == identity and k[1] in parents]
        key = matches[0] if matches else (identity, (path / 'device').resolve().name)
        device = devices.setdefault(key, {'id': identity, 'name': data.get('HID_NAME', ''),
            'bus': 'hid', 'driver': (path / 'device/driver').resolve().name, 'interfaces': []})
        node = devroot / path.name
        device['interfaces'].append({'node': path.name, 'readable': os.access(node, os.R_OK),
                                    'writable': os.access(node, os.W_OK)})
    return list(devices.values())


def inspect(devices=None, catalog=None, lock=None, available=None):
    catalog = catalog if catalog is not None else json.loads((ROOT / 'packaging/device-catalog.json').read_text())
    lock = lock if lock is not None else json.loads((ROOT / 'packaging/logitech-depots.lock.json').read_text())
    selected = set(available if available is not None else lock['selections']['all_devices'])
    from logitech_depots import select_depots
    results, errors, warnings = [], [], []
    by_id = {}
    for model in catalog['devices']:
        for identity in model['identities']:
            by_id.setdefault(identity, []).append(model)
    for device in discover() if devices is None else devices:
        models = by_id.get(device['id'], [])
        # Subdevices share their parent's USB identity. Report the base model,
        # not every optional rim/drive as though it were physically connected.
        primary = [m for m in models if not m.get('subdevice')]
        if primary:
            models = primary
        if not models:
            if device['id'].startswith('046d:') and G_NAME.search(device['name']):
                message = device['name'] + ' is incompatible with the pinned device resources (unrecognized model).'
                errors.append(message)
                results.append({**device, 'compatible': False, 'reason': 'unknown-gaming-device'})
            elif device['id'].startswith('046d:'):
                warnings.append(device['name'] + ': Logitech device/receiver not identified as a specific G HUB model; paired-device coverage is unverified.')
            continue
        required = set()
        for model in models:
            roots = model['depots']
            missing_roots = set(roots) - set(lock['depots'])
            required.update(missing_roots)
            if not missing_roots:
                required.update(select_depots({**lock, 'selections': {'device': roots}}, 'device'))
        missing = sorted(required - selected)
        if any(m.get('non_interactive') for m in models):
            warnings.append(device['name'] + ': catalog recognition only; this model has no configurable G HUB controls.')
        # Multiple keyboard layouts/derivatives share an identity. All variant
        # resources are installed so a locale cannot turn a match into a guess.
        name = ' / '.join(dict.fromkeys(m['name'] for m in models))
        compatible = not missing
        results.append({**device, 'name': name, 'compatible': compatible, 'missing_depots': missing})
        if missing:
            errors.append(name + ' is incompatible with the pinned device resources (missing: ' + ', '.join(missing) + ').')
    return {'devices': results, 'errors': errors, 'warnings': warnings,
            'status': 'FAIL' if errors else 'OK', 'basis': 'pinned resource coverage; physical operation is not certified'}


def present(report, ui):
    ui.step('Verify', 'Checking device compatibility', report['status'])
    for message in report['errors']:
        ui.notice(message, error=True)
    for device in report['devices']:
        if device['compatible']:
            ui.notice(device['name'] + ' is compatible.')
    for message in report['warnings']:
        ui.notice(message)
    if not report['devices']:
        ui.notice('No identifiable Logitech G peripheral is connected; device compatibility remains unverified.')
    ui.notice('Compatibility here means device-resource coverage; it is not a guarantee of every hardware feature.')
