#!/usr/bin/env python3
"""Download and privately stage pinned G HUB vendor depots; never run installers.

The public repository holds URL/hash metadata and this implementation only.
Vendor payloads, manifests and capsule keys stay in external staging.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import time
import urllib.parse

from windows_media_acquire import (ROOT, check_asset, download_asset,
                                  external_path, space_check, work_lock)
from logitech_capsule import decrypt, public_keys, MAX_MEMBER

LOCK_PATH = ROOT / 'packaging/logitech-depots.lock.json'
PLAIN = 0x20170110
CAPSULE = 0x20210506
FILE_CAPSULE = 0x20210521
MAX_INDEX = 4 * 1024**2
MAX_FILES = 20000


def official_url(url):
    u = urllib.parse.urlsplit(url)
    if (u.scheme != 'https' or u.netloc != 'updates.ghub.logitechg.com'
            or not re.fullmatch(r'/depots/[0-9a-f-]{36}/[A-Za-z0-9_-]+\.depot', u.path)
            or u.query or u.fragment):
        raise ValueError('Expected pinned HTTPS Logitech depot URL')
    return url


def relative_name(name):
    if (not isinstance(name, str) or not name or any(c in name for c in '\\:*?"<>|')
            or any(ord(c) < 32 for c in name) or name.startswith('/')
            or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in name.split('/'))):
        raise ValueError(f'Unsafe archive path: {name!r}')
    for part in name.split('/'):
        if part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                          *(f'COM{i}' for i in range(10)),
                                          *(f'LPT{i}' for i in range(10))}:
            raise ValueError('Reserved Windows path')
    return name


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def exact_read(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('Truncated depot')
    return data


def check_names(names):
    files, directories = set(), {}
    for name in names:
        relative_name(name)
        if name.casefold() in files:
            raise ValueError('Duplicate/case-colliding path')
        files.add(name.casefold())
        for parent in PurePosixPath(name).parents:
            key = str(parent).casefold()
            if key in directories and directories[key] != str(parent):
                raise ValueError('Directory-case collision')
            directories[key] = str(parent)
    if files & directories.keys():
        raise ValueError('Archive file/directory collision')


def check_member(data, expected, keys, depth=0):
    """Validate nested resource capsules, but retain their installed encoding."""
    if hashlib.sha256(data).hexdigest() == expected:
        return
    if depth >= 4 or len(data) < 12 or struct.unpack_from('<I', data)[0] != FILE_CAPSULE:
        raise ValueError('Capsule member SHA-256 mismatch')
    length = struct.unpack_from('<I', data, 4)[0]
    if not 0 < length <= min(MAX_INDEX, len(data) - 12):
        raise ValueError('Invalid nested capsule header')
    header = strict_json(data[8:8+length])
    if set(header) != {'key-id', 'file-sha'} or header['file-sha'] != expected:
        raise ValueError('Unexpected nested capsule digest')
    size = struct.unpack_from('<I', data, 8 + length)[0]
    if size != len(data) - 12 - length:
        raise ValueError('Invalid nested capsule bounds')
    plain = decrypt(data[12+length:], expected, header['key-id'], keys)
    check_member(plain, expected, keys, depth + 1)


def inspect_depot(path, spec, keys=None):
    check_asset(path, spec)
    size = path.stat().st_size
    with path.open('rb') as f:
        magic, length = struct.unpack('<II', exact_read(f, 8))
        if not 0 < length <= min(MAX_INDEX, size - 8):
            raise ValueError('Invalid depot index length')
        header = strict_json(exact_read(f, length))
        key_id = None
        if magic == CAPSULE:
            if set(header) != {'header-sha', 'key-id'}:
                raise ValueError('Unexpected encrypted capsule header')
            key_id, expected = header['key-id'], header['header-sha']
            length = struct.unpack('<I', exact_read(f, 4))[0]
            if not 0 < length <= min(MAX_INDEX, size - f.tell()):
                raise ValueError('Invalid encrypted index length')
            decoded = decrypt(exact_read(f, length), expected, key_id, keys or {})
            if hashlib.sha256(decoded).hexdigest() != expected:
                raise ValueError('Capsule index SHA-256 mismatch')
            header = strict_json(decoded)
        if magic not in (PLAIN, CAPSULE) or set(header) != {'files'} or not isinstance(header['files'], list):
            raise ValueError('Unsupported depot format')
        if not 0 < len(header['files']) <= MAX_FILES:
            raise ValueError('Unexpected depot file count')
        records = []
        for item in header['files']:
            if set(item) != ({'name', 'mode', 'sha'} if key_id else {'name', 'mode'}) or not isinstance(item['mode'], int):
                raise ValueError('Unexpected depot file metadata')
            name = relative_name(item['name'])
            if stat.S_IFMT(item['mode']) != stat.S_IFREG:
                raise ValueError('Nonregular archive member')
            length = struct.unpack('<I', exact_read(f, 4))[0]
            if length > size - f.tell() or (key_id and length > MAX_MEMBER):
                raise ValueError('File exceeds depot bounds')
            record = {'path': name, 'offset': f.tell(), 'size': length}
            if key_id:
                if not re.fullmatch('[0-9a-f]{64}', item['sha']):
                    raise ValueError('Invalid member digest')
                record.update(key_id=key_id, content_sha256=item['sha'])
            records.append(record)
            f.seek(length, 1)
        if f.tell() != size:
            raise ValueError('Unexpected trailing depot bytes')
    check_names([r['path'] for r in records])
    if {r['path'] for r in records} != set(spec['expected_files']):
        raise ValueError('Archive differs from pinned vendor file list')
    return {'format': 'encrypted-capsule-20210506' if key_id else 'plain-depot-20170110',
            'status': 'indexed', 'files': records,
            'payload_bytes': sum(r['size'] for r in records)}


def select_depots(lock, selection):
    result, visiting = [], set()
    def visit(name):
        if name in visiting:
            raise ValueError('Dependency cycle')
        if name in result:
            return
        if name not in lock['depots']:
            raise ValueError(f'Unpinned dependency: {name}')
        relative_name(name)
        if '/' in name:
            raise ValueError('Depot name cannot contain folders')
        visiting.add(name)
        for dep in lock['depots'][name]['depends_on']:
            visit(dep)
        visiting.remove(name)
        result.append(name)
    for name in lock['selections'][selection]:
        visit(name)
    return result


def extract_depot(archive, folder, index, keys=None):
    if folder.exists() or folder.is_symlink():
        raise ValueError('Extract only into a new directory')
    space_check(folder.parent, index['payload_bytes'])
    folder.mkdir(mode=0o700)
    records = []
    with archive.open('rb') as source:
        for item in index['files']:
            target = folder / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            source.seek(item['offset'])
            remaining = item['size']
            h = hashlib.sha256()
            if 'key_id' in item:
                data = decrypt(exact_read(source, remaining), item['content_sha256'], item['key_id'], keys or {})
                check_member(data, item['content_sha256'], keys or {})
                space_check(folder, len(data))
                with target.open('xb') as out:
                    out.write(data)
                remaining = 0
                h.update(data)
            else:
                with target.open('xb') as out:
                    while remaining:
                        data = exact_read(source, min(4 * 1024**2, remaining))
                        space_check(folder, len(data))
                        out.write(data)
                        h.update(data)
                        remaining -= len(data)
            os.chmod(target, 0o600)
            records.append({'path': item['path'], 'size': item['size'], 'sha256': h.hexdigest()})
    return records


def plan_layout(output, extracted):
    """Resolve application-root copy rules, leave installer actions reviewable."""
    targets, pending = {}, []
    for depot, records in extracted.items():
        source_dir = output / 'depots' / depot
        index = {r['path']: r for r in records}
        if 'manifest.json' not in index:
            continue  # Resource-only depots legitimately have no installer manifest.
        manifest = strict_json((source_dir / 'manifest.json').read_bytes())
        installer = manifest.get('installer', {})
        for key, value in installer.items():
            if key != 'files':
                pending.append({'depot': depot, 'kind': key, 'declaration': value,
                                'status': 'recorded_only_not_executed'})
        for rule in installer.get('files', []):
            source, dest = relative_name(rule['source']), rule['destination']
            if (rule.get('fileGroup') or not isinstance(dest, str)
                    or not re.match(r'^\{(?:install_root|backend_root)\}(?:/|$)', dest)):
                pending.append({'depot': depot, 'kind': 'conditional_or_external_file', 'declaration': rule,
                                'status': 'recorded_only_not_executed'})
                continue
            tail = dest.split('}', 1)[1].lstrip('/')
            if tail:
                relative_name(tail.rstrip('/'))
            if source in index:
                destination = tail if tail and not dest.endswith('/') else str(PurePosixPath(tail) / PurePosixPath(source).name)
                pairs = [(source, destination)]
            else:
                pairs = [(name, str(PurePosixPath(tail) / name[len(source)+1:]))
                         for name in index if name.startswith(source + '/')]
                if not pairs:
                    # The pinned core manifest references Trueforce SDK files
                    # absent from its own archive. Preserve this observable gap.
                    pending.append({'depot': depot, 'kind': 'missing_source_file', 'declaration': rule,
                                    'status': 'source_not_in_depot_requires_extension_investigation'})
                    continue
            for src, dst in pairs:
                relative_name(dst)
                folded = dst.casefold()
                record = {'depot': depot, 'source': src, 'destination': dst,
                          'size': index[src]['size'], 'sha256': index[src]['sha256']}
                if folded in targets and (targets[folded]['sha256'] != record['sha256']
                                          or targets[folded]['destination'] != dst):
                    raise ValueError(f'Conflicting application destination: {dst}')
                targets[folded] = record
            if rule.get('registerLibrary'):
                pending.append({'depot': depot, 'kind': 'registerLibrary', 'declaration': rule,
                                'status': 'recorded_only_not_executed'})
    # Directory case aliases also matter when a staged Linux tree becomes Wine data.
    dirs = {}
    for record in targets.values():
        for parent in PurePosixPath(record['destination']).parents:
            key = str(parent).casefold()
            if key in targets or (key in dirs and dirs[key] != str(parent)):
                raise ValueError('Application file/directory or directory-case collision')
            dirs[key] = str(parent)
    return {'files': sorted(targets.values(), key=lambda r: r['destination']),
            'pending_installer_actions': pending, 'actions_executed': False}


def lock_digest(lock):
    return hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def staged_files(folder):
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Missing or symlinked stage directory')
    actual = set()
    for path in folder.rglob('*'):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('Nonregular entry in stage')
        if path.is_file():
            actual.add(path.relative_to(folder).as_posix())
    return actual


def verify_stage(output, lock):
    staged_files(output)  # Includes parents, report and unexpected subtrees.
    report = strict_json((output / 'extraction.json').read_text())
    if report['lock_sha256'] != lock_digest(lock):
        raise ValueError('Stage was produced with a different lock')
    if set(report['extracted']) != set(select_depots(lock, report['selection'])):
        raise ValueError('Stage has missing or unexpected depots')
    count = 0
    for depot, records in report['extracted'].items():
        folder = output / 'depots' / depot
        actual = staged_files(folder)
        check_names([item['path'] for item in records])
        if (actual != set(lock['depots'][depot]['expected_files'])
                or actual != {item['path'] for item in records}):
            raise ValueError('Staged file set differs from pinned vendor file list')
        for item in records:
            check_asset(folder / relative_name(item['path']), item)
            count += 1
    layout = report['layout']
    if layout != plan_layout(output, report['extracted']):
        raise ValueError('Application layout differs from extracted manifests')
    application = output / 'application/LGHUB'
    if staged_files(application) != {item['destination'] for item in layout['files']}:
        raise ValueError('Unexpected or missing application files')
    for item in layout['files']:
        check_asset(output / 'application/LGHUB' / relative_name(item['destination']), item)
    return {'depot_files_verified': count, 'application_files_verified': len(layout['files']),
            'capsules_pending': 0,
            'application_version': lock['application_version'], 'installer_actions_executed': False}


def stage(work, output, lock, selection):
    if output.exists() or output.is_symlink() or work.is_relative_to(output):
        raise ValueError('Stage must be a new directory outside the download cache ancestors')
    names = select_depots(lock, selection)
    keys = public_keys(work, lock['public_access']) if selection == 'tested_setup' else {}
    indexes = {name: inspect_depot(work / lock['depots'][name]['local_name'], lock['depots'][name], keys)
               for name in names}
    # At most one additional application copy of each payload byte.
    space_check(output.parent, 2 * sum(v.get('payload_bytes', 0) for v in indexes.values()))
    output.mkdir(mode=0o700)
    (output / 'depots').mkdir()
    extracted = {}
    for name, index in indexes.items():
        print(f'Extracting {name}: {len(index["files"])} files', flush=True)
        extracted[name] = extract_depot(work / lock['depots'][name]['local_name'], output / 'depots' / name, index, keys)
    layout = plan_layout(output, extracted)
    (output / 'application/LGHUB').mkdir(parents=True)
    for item in layout['files']:
        destination = output / 'application/LGHUB' / item['destination']
        destination.parent.mkdir(parents=True, exist_ok=True)
        space_check(output, item['size'])
        with (output / 'depots' / item['depot'] / item['source']).open('rb') as src, destination.open('xb') as dst:
            shutil.copyfileobj(src, dst, 4 * 1024**2)
        os.chmod(destination, 0o600)
    report = {'schema': 1, 'application_version': lock['application_version'], 'build_id': lock['build_id'],
              'selection': selection, 'lock_sha256': lock_digest(lock),
              'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'downloaded_depots': len(names), 'extracted_depots': len(extracted),
              'extracted': extracted, 'layout': layout,
              'runtime_modified': False, 'vendor_executables_started': False}
    (output / 'extraction.json').write_text(json.dumps(report, indent=2) + '\n')
    verified = verify_stage(output, lock)
    (output / 'verification.json').write_text(json.dumps(verified, indent=2) + '\n')
    print(json.dumps(verified, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('fetch', 'stage'):
        p = sub.add_parser(command)
        p.add_argument('--work-dir', required=True)
        p.add_argument('--selection', choices=('core', 'required', 'tested_setup'), default='tested_setup')
        if command == 'stage':
            p.add_argument('--output-dir', required=True)
    p = sub.add_parser('verify')
    p.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    lock = json.loads(LOCK_PATH.read_text())
    if args.command == 'verify':
        print(json.dumps(verify_stage(external_path(args.output_dir), lock), indent=2))
        return
    work = external_path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    with work_lock(work):
        if args.command == 'fetch':
            for name in select_depots(lock, args.selection):
                download_asset(work, lock['depots'][name], validate=official_url)
            if args.selection == 'tested_setup':
                public_keys(work, lock['public_access'], fetch=True)
        else:
            stage(work, external_path(args.output_dir), lock, args.selection)


if __name__ == '__main__':
    main()
