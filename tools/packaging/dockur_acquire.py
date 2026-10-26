#!/usr/bin/env python3
"""Private, version-pinned Dockur acquisition experiment (Python standard library).

No command modifies a Wine prefix or publishes collected Windows files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import fcntl
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'packaging/dockur'
LOCK = json.loads((ASSETS / 'lock.json').read_text())
GIB = 1024 ** 3
FLOOR = 20 * GIB
LABEL = 'org.penguin-lghub.acquisition'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def official_url(url):
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or '').lower()
    if (u.scheme != 'https' or u.username or u.password or u.port not in (None, 443)
            or not (host == 'microsoft.com' or host.endswith('.microsoft.com'))):
        raise ValueError('Only HTTPS microsoft.com media URLs and redirects are accepted')
    return url


class MicrosoftRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def space_check(paths, reserve=0):
    for path in paths:
        if shutil.disk_usage(path).free < FLOOR + reserve:
            raise RuntimeError(f'Insufficient disk space at {path}; retain 20 GiB plus reserved output')


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, text=True, **kwargs)


def download(args):
    """Explicit registration acknowledgement; never invoke Dockur's mirror downloader."""
    if not args.evaluation_registered:
        raise ValueError('Complete Microsoft evaluation registration, then supply --evaluation-registered')
    url = official_url(args.url)
    target = Path(args.output).expanduser().resolve()
    if target.is_relative_to(ROOT):
        raise ValueError('Keep Windows media outside the source repository')
    if target.exists():
        raise ValueError('Output already exists; refusing to overwrite media')
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.partial')
    opener = urllib.request.build_opener(MicrosoftRedirects())
    with opener.open(url, timeout=60) as response:
        final_url = official_url(response.url)
        size = int(response.headers.get('Content-Length', 0))
        if not 1024 ** 3 <= size <= 12 * GIB:
            raise ValueError('Expected a 1–12 GiB Windows ISO with Content-Length; review changed source')
        space_check([target.parent], size)
        count = 0
        with partial.open('xb') as f:
            while chunk := response.read(4 * 1024 * 1024):
                count += len(chunk)
                if count > size:
                    raise ValueError('Download exceeded declared size')
                space_check([target.parent])
                f.write(chunk)
        if count != size:
            raise ValueError('Truncated ISO download; partial file retained for diagnosis')
    digest = sha256(partial)
    if args.sha256 and digest != args.sha256.lower():
        raise ValueError('ISO differs from supplied expected SHA-256; partial file retained')
    partial.replace(target)
    record = {'url': url, 'final_url': final_url, 'size': size, 'sha256': digest,
              'hash_basis': 'compared to operator-supplied hash' if args.sha256 else 'locally computed, not publisher-verified',
              'license_basis': 'registered evaluation; external component use unresolved'}
    write_json(target.with_name(target.name + '.provenance.json'), record)
    print(json.dumps(record, indent=2))


def config(work, iso, data):
    token = secrets.token_hex(12)
    tag = hashlib.sha256(str(work).encode()).hexdigest()[:12]
    return {
        'name': 'penguin-acquire-' + tag,
        'services': {'windows': {
            'image': LOCK['image'], 'platform': 'linux/amd64',
            'labels': {LABEL: str(work)},
            'devices': ['/dev/kvm:/dev/kvm', '/dev/net/tun:/dev/net/tun'],
            'cap_add': ['NET_ADMIN'], 'cpus': 2, 'mem_limit': '6g',
            'environment': {
                'RAM_SIZE': '4G', 'CPU_CORES': '2', 'DISK_SIZE': '64G',
                'DISK_FMT': 'qcow2', 'ALLOCATE': 'N', 'DISK_IO': 'threads',
                'DISK_CACHE': 'writeback', 'VERSION': '11', 'REMOVE': 'N',
                'USERNAME': 'PenguinTest', 'PASSWORD': token, 'PROTECT': 'Y',
                'GPU': 'N', 'AUDIO': 'N', 'DISPLAY': 'web', 'MIDO': 'N', 'ESD': 'N'},
            'ports': ['127.0.0.1:18006:8006'],
            'volumes': [
                {'type': 'bind', 'source': str(data / 'storage'), 'target': '/storage'},
                {'type': 'bind', 'source': str(data / 'shared'), 'target': '/shared'},
                {'type': 'bind', 'source': str(work / 'oem'), 'target': '/oem', 'read_only': True},
                {'type': 'bind', 'source': str(iso), 'target': '/custom.iso', 'read_only': True}],
            'restart': 'no', 'stop_grace_period': '60s',
            'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '2'}}}},
        # Explicit ISO plus no internet: no hidden OS fallback downloads, no updates during capture.
        'networks': {'default': {'internal': True}}}


def prepare(args):
    iso = Path(args.iso).expanduser().resolve(strict=True)
    work = Path(args.workspace).expanduser().resolve()
    data = Path(args.data_dir).expanduser().resolve()
    if data.is_relative_to(ROOT) or data.is_relative_to(work) or work.is_relative_to(data) or iso.is_relative_to(data):
        raise ValueError('Use a separate external data directory, control workspace and ISO')
    if work.is_relative_to(ROOT) or work in iso.parents or iso.is_relative_to(work):
        raise ValueError('Use separate external workspace and ISO paths')
    if work.exists() or data.exists():
        raise ValueError('Workspace already exists; choose a new directory to preserve prior evidence')
    if not re.fullmatch('[0-9a-fA-F]{64}', args.sha256):
        raise ValueError('Supply the exact ISO SHA-256')
    if sha256(iso) != args.sha256.lower():
        raise ValueError('ISO SHA-256 mismatch')
    work.parent.mkdir(parents=True, exist_ok=True)
    # 64 GiB guest + media repacking overhead; existing source ISO is already allocated.
    data.parent.mkdir(parents=True, exist_ok=True)
    space_check([data.parent], 80 * GIB)
    space_check([work.parent])
    os.umask(0o077)
    work.mkdir(mode=0o700)
    data.mkdir()
    for name in ('storage', 'shared'):
        (data / name).mkdir()
    (work / 'oem').mkdir()
    for name in ('install.bat', 'collect.ps1', 'lock.json'):
        shutil.copyfile(ASSETS / name, work / 'oem' / name)
    metadata = {'schema': 1, 'workspace': str(work), 'data_dir': str(data), 'iso': str(iso),
                'iso_sha256': args.sha256.lower(), 'license_basis': args.license_basis,
                'image': LOCK['image'], 'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    write_json(work / 'compose.json', config(work, iso, data))
    os.chmod(work / 'compose.json', 0o600)
    if (work / 'compose.json').stat().st_mode & 0o077:
        raise RuntimeError('Control workspace must support private Unix file permissions')
    metadata['compose_sha256'] = sha256(work / 'compose.json')
    metadata['oem_sha256'] = {p.name: sha256(p) for p in (work / 'oem').iterdir()}
    write_json(work / 'acquisition.json', metadata)
    run(['docker', 'compose', '-f', str(work / 'compose.json'), 'config', '--quiet'])
    print(f'Prepared {work}. Windows has not been started; no license was accepted.')


def load_work(path):
    work = Path(path).expanduser().resolve(strict=True)
    meta = json.loads((work / 'acquisition.json').read_text())
    if meta['workspace'] != str(work) or meta['image'] != LOCK['image']:
        raise ValueError('Workspace ownership/image mismatch')
    if sha256(work / 'compose.json') != meta['compose_sha256']:
        raise ValueError('Compose configuration changed after preparation')
    for name, digest in meta['oem_sha256'].items():
        if sha256(work / 'oem' / name) != digest:
            raise ValueError('Guest collection scripts changed after preparation')
    return work, meta


def container_id(work):
    ids = run(['docker', 'compose', '-f', str(work / 'compose.json'), 'ps', '-aq', 'windows'],
              capture_output=True).stdout.split()
    if len(ids) != 1:
        raise RuntimeError('Expected exactly one owned acquisition container')
    info = json.loads(run(['docker', 'inspect', ids[0]], capture_output=True).stdout)[0]
    if info['Config']['Labels'].get(LABEL) != str(work):
        raise RuntimeError('Container ownership label mismatch')
    return ids[0], info


def verify_collection(work):
    meta = json.loads((work / 'acquisition.json').read_text())
    folder = Path(meta['data_dir']) / 'shared/collected'
    manifest = json.loads((folder / 'guest-manifest.json').read_text(encoding='utf-8-sig'))
    expected = set(LOCK['baseline_sha256']) | set(LOCK['fonts'])
    files = manifest['files']
    if len(files) != len(expected) or {f['name'] for f in files} != expected:
        raise ValueError('Guest collection is incomplete or contains unexpected file names')
    results = []
    for entry in files:
        path = folder / entry['name']
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing file or unexpected symlink in collection')
        digest = sha256(path)
        if digest != entry['sha256'] or path.stat().st_size != entry['size']:
            raise ValueError('Host/guest file integrity mismatch: ' + entry['name'])
        baseline = LOCK['baseline_sha256'].get(entry['name'])
        results.append({'name': entry['name'], 'sha256': digest,
                        'baseline_match': digest == baseline if baseline else None})
    report = {'files': results, 'collected_files': len(results),
              'dll_baseline_matches': sum(f['baseline_match'] is True for f in results),
              'dll_baseline_total': len(LOCK['baseline_sha256']),
              'wine_compatibility_tested': False, 'license_clearance': 'unresolved',
              'fonts': 'integrity checked only; baseline/font-rendering comparison pending'}
    write_json(work / 'comparison.json', report)
    return report


def supervise(args):
    work, meta = load_work(args.workspace)
    guard_lock = (work / 'supervisor.lock').open('w')
    fcntl.flock(guard_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    if not args.accept_windows_license:
        raise ValueError('Review the selected media license before using --accept-windows-license; Dockur automates Windows Setup acceptance')
    if sha256(meta['iso']) != meta['iso_sha256']:
        raise ValueError('Media changed after preparation')
    if args.max_seconds <= 0:
        raise ValueError('max-seconds must be positive')
    data = Path(meta['data_dir'])
    space_check([data], 80 * GIB)
    docker_root = run(['docker', 'info', '--format', '{{.DockerRootDir}}'], capture_output=True).stdout.strip()
    space_check([docker_root])
    avail = int(re.search(r'MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text())[1]) * 1024
    if avail < 8 * GIB:
        raise RuntimeError('Less than 8 GiB available; postpone the 4 GiB guest')
    if (data / 'shared/collection.complete').exists():
        raise RuntimeError('Previous collection exists; use collect or create a new workspace')
    compose = ['docker', 'compose', '-f', str(work / 'compose.json')]
    start = time.monotonic()
    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    run_record = {'phase': 'starting', 'license_acceptance_instructed': True,
                  'license_basis': meta['license_basis'], 'iso_sha256': meta['iso_sha256'],
                  'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    write_json(work / 'run.json', run_record)
    cid = None
    try:
        run(compose + ['up', '-d', '--pull', 'never'])
        cid, _ = container_id(work)
        while True:
            space_check([data, work, docker_root])
            if (data / 'shared/collection.error').exists():
                raise RuntimeError('Guest collection failed; inspect shared/collection.error')
            if (data / 'shared/collection.complete').exists():
                print(json.dumps(verify_collection(work), indent=2))
                run_record['phase'] = 'collected_and_integrity_checked'
                break
            _, info = container_id(work)
            if not info['State']['Running']:
                raise RuntimeError('Guest container exited before completing collection')
            if time.monotonic() - start > args.max_seconds:
                raise RuntimeError('Automated installation exceeded its runtime limit; evidence retained')
            write_json(work / 'progress.json', {'phase': 'installing_or_collecting',
                'elapsed_seconds': round(time.monotonic() - start),
                'free_gib': round(shutil.disk_usage(data).free / GIB, 1)})
            time.sleep(5)
    finally:
        if run_record['phase'] == 'starting':
            run_record['phase'] = 'stopped_before_collection'
        run_record['elapsed_seconds'] = round(time.monotonic() - start)
        # Stop only this run's container; never operate on the user's other VMs or Wine.
        if cid is None:
            try:
                cid, _ = container_id(work)
            except (RuntimeError, subprocess.CalledProcessError):
                pass
        if cid:
            with (work / 'container.log').open('w') as log:
                subprocess.run(['docker', 'logs', cid], stdout=log, stderr=subprocess.STDOUT, text=True)
            subprocess.run(['docker', 'stop', '-t', '30', cid], check=False)
        write_json(work / 'run.json', run_record)


def preflight(args):
    """Exercise the real image, Compose parser and KVM without Windows media."""
    probe = run(['docker', 'run', '--rm', '-i', '--pull', 'never', '--network', 'none',
        '--device', '/dev/kvm', '--cpus', '1', '--memory', '512m',
        '--entrypoint', 'qemu-system-x86_64', LOCK['image'],
        '-machine', 'q35,accel=kvm', '-m', '128', '-smp', '1',
        '-nodefaults', '-display', 'none', '-S', '-qmp', 'stdio'],
        input='{"execute":"qmp_capabilities"}\n{"execute":"query-kvm"}\n{"execute":"quit"}\n',
        capture_output=True, timeout=30)
    events = [json.loads(line) for line in probe.stdout.splitlines() if line.strip()]
    kvm = next((e['return'] for e in events if isinstance(e.get('return'), dict) and 'enabled' in e['return']), {})
    if not kvm.get('enabled'):
        raise RuntimeError('KVM did not enable inside the pinned image')
    with tempfile.TemporaryDirectory(prefix='penguin-compose-check-') as directory:
        root = Path(directory)
        compose = root / 'compose.json'
        write_json(compose, config(root / 'control', root / 'test.iso', root / 'data'))
        run(['docker', 'compose', '-f', str(compose), 'config', '--quiet'])
    result = {'image': LOCK['image'], 'kvm': kvm, 'compose_valid': True,
              'windows_boot_tested': False, 'guest_collection_tested': False,
              'wine_compatibility_tested': False, 'license_clearance': 'unresolved'}
    if args.report:
        report = Path(args.report).expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        write_json(report, result)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('preflight', help='Test the pinned container and KVM without installing Windows')
    p.add_argument('--report', help='Optional evidence JSON path')
    p = commands.add_parser('download', help='Download registered evaluation media only from Microsoft')
    p.add_argument('--url', default=LOCK['observed_evaluation_url'])
    p.add_argument('--output', required=True)
    p.add_argument('--sha256', help='Expected hash, if supplied by Microsoft')
    p.add_argument('--evaluation-registered', action='store_true')
    p = commands.add_parser('prepare', help='Prepare a new external workspace without starting Windows')
    p.add_argument('--data-dir', required=True, help='New directory on a spacious disk; Windows disks and collected files stay here')
    p.add_argument('--iso', required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--workspace', required=True)
    p.add_argument('--license-basis', choices=['registered-evaluation', 'separately-licensed-windows'], required=True)
    p = commands.add_parser('run', help='Run under a disk guard; stop after collection or failure')
    p.add_argument('--workspace', required=True)
    p.add_argument('--accept-windows-license', action='store_true')
    p.add_argument('--max-seconds', type=int, default=14400)
    p = commands.add_parser('collect', help='Verify existing evidence without starting a VM')
    p.add_argument('--workspace', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'preflight': preflight(args)
        elif args.command == 'download': download(args)
        elif args.command == 'prepare': prepare(args)
        elif args.command == 'run': supervise(args)
        else: print(json.dumps(verify_collection(load_work(args.workspace)[0]), indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Acquisition stopped: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
