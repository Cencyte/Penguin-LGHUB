#!/usr/bin/env python3
"""Fetch pinned official Windows acquisition inputs, or verify reconstructed DLLs.

This stages private files only. It never modifies a Wine runtime or proves license
clearance. No third-party download mirrors or unverified version substitutions.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import struct
import time
import urllib.parse
import urllib.request
import urllib.error
import ssl
from install_storage import FREE_SPACE_FLOOR

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / 'packaging/windows-acquisition.lock.json'
FLOOR = FREE_SPACE_FLOOR


def official_url(url):
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or '').lower()
    if (u.scheme != 'https' or u.username or u.password or u.port not in (None, 443)
            or not (host == 'microsoft.com' or host.endswith('.microsoft.com'))):
        raise ValueError('Expected an HTTPS Microsoft download URL')
    return url


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, validate=official_url):
        super().__init__()
        self.validate = validate

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.validate(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def digest_file(path, algorithms=('sha256',)):
    hashes = {alg: hashlib.new(alg) for alg in algorithms}
    with path.open('rb') as f:
        for data in iter(lambda: f.read(4 * 1024**2), b''):
            for h in hashes.values():
                h.update(data)
    return {alg: h.hexdigest() for alg, h in hashes.items()}


def space_check(path, remaining=0):
    if shutil.disk_usage(path).free < FLOOR + remaining:
        raise RuntimeError('Insufficient space: reserve pending output plus 2 GiB of free headroom')


def check_asset(path, asset):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != asset['size']:
        raise ValueError(f'Unexpected file type or size: {path}')
    algorithms = tuple(a for a in ('sha1', 'sha256') if a in asset)
    if not algorithms:
        raise ValueError('A pinned expected checksum is required')
    hashes = digest_file(path, tuple(set(algorithms) | {'sha256'}))
    for alg in algorithms:
        if hashes[alg] != asset[alg].lower():
            raise ValueError(f'{alg} mismatch: {path}')
    return hashes


class InterruptedDownload(ValueError):
    """An incomplete transfer can be resumed; an integrity failure cannot."""


def download_asset(work, asset, opener=None, validate=official_url, *, attempts=6):
    """Retry transient network failures before the installer rolls back.

    Every attempt revalidates the partial-file boundary and HTTP range. Full
    size and digest checks remain mandatory before a file is promoted.
    """
    if not 1 <= attempts <= 6:
        raise ValueError('Download attempts must be between one and six')
    for attempt in range(1, attempts + 1):
        try:
            return _download_asset_once(work, asset, opener, validate)
        except (InterruptedDownload, http.client.IncompleteRead,
                http.client.RemoteDisconnected, ConnectionError, TimeoutError,
                urllib.error.URLError) as error:
            if isinstance(error, urllib.error.HTTPError):
                if error.code not in (408, 429, 500, 502, 503, 504):
                    raise
            elif isinstance(error, urllib.error.URLError):
                if isinstance(error.reason, ssl.SSLCertVerificationError):
                    raise
            if attempt == attempts:
                raise InterruptedDownload(f'{asset["local_name"]}: transfer failed after '
                                          f'{attempts} attempts ({error}); partial retained until transaction cleanup') from error
            partial = work / (asset['local_name'] + '.partial')
            count = partial.stat().st_size if partial.is_file() and not partial.is_symlink() else 0
            delay = min(2 ** (attempt - 1), 8)
            print(f'{asset["local_name"]}: transfer interrupted at {count}/{asset["size"]} bytes; '
                  f'retrying {attempt+1}/{attempts} in {delay}s', flush=True)
            time.sleep(delay)


def _download_asset_once(work, asset, opener=None, validate=official_url):
    name = asset['local_name']
    if Path(name).name != name or name in ('', '.', '..'):
        raise ValueError('Download must have a plain file name')
    url = validate(asset['url'])
    target = work / name
    if target.exists() or target.is_symlink():
        hashes = check_asset(target, asset)
        print(f'Verified existing {name}', flush=True)
        return hashes
    partial = target.with_name(name + '.partial')
    if partial.is_symlink() or (partial.exists() and not partial.is_file()):
        raise ValueError('Unexpected partial-file type')
    size = asset['size']
    count = partial.stat().st_size if partial.exists() else 0
    if count > size:
        raise ValueError('Partial file exceeds expected size')
    space_check(work, size - count)
    if count < size:
        headers = {'Range': f'bytes={count}-'} if count else {}
        opener = opener or urllib.request.build_opener(OfficialRedirects(validate))
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=45) as response:
            final_url = validate(response.url)
            if response.status != (206 if count else 200):
                raise ValueError('Server did not honor the requested download/range')
            if count and response.headers.get('Content-Range') != f'bytes {count}-{size-1}/{size}':
                raise ValueError('Unexpected resume Content-Range')
            length = response.headers.get('Content-Length')
            # A chunked HTTP response legitimately omits Content-Length (for
            # example GitHub's source archives). Bound the decoded stream by
            # the pinned size below, then verify its full digest before promotion.
            if length is not None and int(length) != size - count:
                raise ValueError(f'Unexpected response length for {name}: '
                                 f'expected {size-count}, received {length}')
            started = last = time.monotonic()
            initial = count
            with partial.open('ab' if partial.exists() else 'xb') as out:
                while chunk := response.read(4 * 1024**2):
                    count += len(chunk)
                    if count > size:
                        raise ValueError('Download exceeds pinned size')
                    space_check(work, size - count + len(chunk))
                    out.write(chunk)
                    now = time.monotonic()
                    if now - last >= 20:
                        print(f'{name}: {count/size:.1%}, '
                              f'{(count-initial)/(now-started)/1024**2:.1f} MiB/s', flush=True)
                        last = now
            if count != size:
                raise InterruptedDownload(f'Truncated download: {name}, received {count}/{size} bytes')
    else:
        final_url = url
    hashes = check_asset(partial, asset)
    partial.rename(target)
    record = {'source_url': url, 'final_url': final_url, 'size': size, **hashes,
              'matched_pinned_checksums': True,
              'retrieved_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    target.with_name(name + '.provenance.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f'Verified downloaded {name}', flush=True)
    return hashes


def pe_info(path):
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Not a PE executable')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if pe + 84 > len(data) or data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Invalid PE header')
    machine = struct.unpack_from('<H', data, pe+4)[0]
    # These pinned files have one VS_FIXEDFILEINFO. Its signature and structure
    # version are checked together; ambiguous data are rejected, not guessed.
    signature = b'\xbd\x04\xef\xfe\x00\x00\x01\x00'
    if data.count(signature) != 1:
        raise ValueError('Missing or ambiguous VS_FIXEDFILEINFO')
    pos = data.index(signature)
    if pos + 52 > len(data):
        raise ValueError('Truncated VS_FIXEDFILEINFO')
    high, low = struct.unpack_from('<II', data, pos+8)
    return {'machine': f'0x{machine:04x}',
            'version': '.'.join(map(str, (high >> 16, high & 65535, low >> 16, low & 65535)))}


def verify_dlls(folder, lock):
    results = []
    for name, expected in lock['files'].items():
        path = folder / name
        hashes = check_asset(path, expected)
        info = pe_info(path)
        if info['machine'] != expected['machine'] or info['version'] != expected['version']:
            raise ValueError(f'PE architecture/version mismatch: {name}')
        results.append({'name': name, 'size': path.stat().st_size, **hashes, **info,
                        'exact_baseline_match': True})
    return {'schema': 1, 'files': results, 'exact_matches': len(results),
            'required_dlls': len(lock['files']), 'runtime_modified': False,
            'license_clearance': 'unresolved; hashes establish identity, not permission'}


def external_path(value):
    work = Path(value).expanduser().resolve()
    if work == Path('/') or work == Path.home() or work.is_relative_to(ROOT):
        raise ValueError('Use a dedicated acquisition directory outside the repository')
    if 'drive_c' in work.parts or any((p / 'system.reg').exists() or (p / 'drive_c').exists()
                                     for p in (work, *work.parents)):
        raise ValueError('Acquisition directory must not be inside an existing Wine prefix')
    return work


@contextmanager
def work_lock(work, *, busy_message=None):
    # One downloader/reconstructor per cache, including resumed partial files.
    fd = os.open(work / '.acquisition.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            if busy_message is None:
                raise
            raise RuntimeError(busy_message) from error
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    fetch = sub.add_parser('fetch', help='Download/verify pinned package and installation ESD')
    fetch.add_argument('--work-dir', required=True)
    fetch.add_argument('--decoder', action='store_true', help='Also fetch the pinned reconstruction decoder source')
    verify = sub.add_parser('verify', help='Verify both reconstructed DLLs, including PE metadata')
    verify.add_argument('--directory', required=True)
    reconstruct = sub.add_parser('reconstruct', help='Rebuild exact DLLs from verified media in fresh private directories')
    reconstruct.add_argument('--work-dir', required=True, help='Existing downloaded media cache')
    reconstruct.add_argument('--output-dir', required=True, help='New directory for extracted inputs and DLLs')
    reconstruct.add_argument('--control-dir', required=True, help='New local Unix-filesystem directory for helper/Wine prefix')
    reconstruct.add_argument('--wine', default='wine', help='Acquisition Wine executable; tested with stock Wine 10.20')
    args = parser.parse_args()
    lock = json.loads(LOCK_PATH.read_text())
    if args.command == 'verify':
        print(json.dumps(verify_dlls(Path(args.directory), lock), indent=2))
        return
    work = external_path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    with work_lock(work):
        if args.command == 'reconstruct':
            from windows_media_reconstruct import reconstruct
            reconstruct(work, external_path(args.output_dir), external_path(args.control_dir), lock, args.wine)
        else:
            for key in ('package', 'base_media'):
                download_asset(work, lock[key])
            if args.decoder:
                from windows_media_reconstruct import tool_source_url
                download_asset(work, lock['extraction_tools']['ms_compress'], validate=tool_source_url)


if __name__ == '__main__':
    main()
