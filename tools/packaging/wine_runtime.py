#!/usr/bin/env python3
"""Assemble a private, relocatable Wine 10.20 runtime without installing packages.

The full signed Arch runtime payload is combined with pinned project artifacts.
Unix libraries/graphics drivers are audited host dependencies, not yet a bundled
Linux userspace. No vendor G HUB or Windows binaries belong in this bundle.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import urllib.parse

from windows_media_acquire import ROOT, check_asset, digest_file, download_asset, external_path, space_check, work_lock

LOCK = ROOT / 'packaging/wine-runtime.lock.json'
TREES = ('usr/bin/', 'usr/lib/wine/', 'usr/share/wine/')


def official_url(url):
    u = urllib.parse.urlsplit(url)
    if (u.scheme != 'https' or u.netloc != 'archive.archlinux.org'
            or not u.path.startswith('/packages/w/wine/') or u.query or u.fragment):
        raise ValueError('Expected a pinned HTTPS Arch Wine archive URL')
    return url


def safe_name(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name:
        raise ValueError('Unsafe runtime member path')
    return p


def unpack(stream, output, bundle=False):
    """Extract only runtime trees, never package installation hooks or devices."""
    seen = set()
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            name = str(safe_name(member.name))
            if bundle:
                pass  # The complete project archive also contains its receipt and supplements.
            elif name in ('.PKGINFO', '.BUILDINFO'):
                name = 'provenance/' + name[1:]
            elif not name.startswith(TREES):
                continue
            if name in seen:
                raise ValueError('Duplicate runtime archive member')
            seen.add(name)
            target = output / name
            if any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(output)):
                raise ValueError('Archive writes through a symlink')
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            elif member.issym():
                link = safe_name(member.linkname)
                if not (target.parent / link).resolve().is_relative_to(output):
                    raise ValueError('External runtime symlink')
                target.symlink_to(str(link))
            elif member.isfile():
                space_check(output, member.size)
                with archive.extractfile(member) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 4 * 1024**2)
                target.chmod(member.mode & 0o777 if bundle else (0o755 if member.mode & 0o111 else 0o644))
            else:
                raise ValueError('Unsupported runtime member type')
    return seen


def inventory(root):
    files = {}
    for path in sorted(root.rglob('*')):
        if path == root / 'runtime.json':
            continue
        name = str(path.relative_to(root))
        if path.is_symlink():
            if not path.resolve(strict=True).is_relative_to(root):
                raise ValueError('External runtime symlink')
            files[name] = {'link': os.readlink(path)}
        elif path.is_file():
            files[name] = {**digest_file(path), 'size': path.stat().st_size,
                           'mode': path.stat().st_mode & 0o777}
        elif not path.is_dir():
            raise ValueError('Nonregular runtime entry')
    return files


def verify_runtime(root):
    root = root.resolve(strict=True)
    report = json.loads((root / 'runtime.json').read_text())
    if report['schema'] != 1 or report['kind'] != 'penguin-private-wine-runtime':
        raise ValueError('Not a private Wine runtime')
    if report['lock_sha256'] != digest_file(LOCK)['sha256']:
        raise ValueError('Runtime uses a different package/artifact lock')
    current = inventory(root)
    if current != report['files']:
        raise ValueError('Runtime file inventory changed')
    lock = json.loads(LOCK.read_text())
    for category in ('patches', 'markers', 'supplements'):
        for spec in lock[category].values():
            if current.get(spec['target'], {}).get('sha256') != spec['sha256']:
                raise ValueError('Runtime no longer matches a pinned compatibility artifact')
    if report['supplements'] != lock['supplements']:
        raise ValueError('Runtime supplementary DLL mapping changed')
    return report


def runtime_environment(root, prefix, env):
    root, prefix = root.resolve(), prefix.resolve()
    clean = {k: v for k, v in env.items() if not k.startswith(('WINE', 'WINEDLL', 'LD_'))}
    clean.update(PATH=str(root / 'usr/bin') + ':' + env.get('PATH', '/usr/bin:/bin'),
                 WINEPREFIX=str(prefix), WINEARCH='win64',
                 WINESERVER=str(root / 'usr/bin/wineserver'),
                 WINELOADER=str(root / 'usr/lib/wine/x86_64-unix/wine'),
                 WINEDLLPATH=str(root / 'usr/lib/wine'),
                 WINEDEBUG=env.get('WINEDEBUG', '-all'),
                 WINEDLLOVERRIDES=env.get('WINEDLLOVERRIDES', ''),
                 LD_LIBRARY_PATH=str(root / 'usr/lib/wine/x86_64-unix'))
    return clean


def audit_unix(root):
    """Inspect every ELF module, including modules not hit by a short smoke test."""
    modules = {}
    glibc = set()
    for path in sorted((root / 'usr').rglob('*')):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as f:
            if f.read(4) != b'\x7fELF':
                continue
        dynamic = subprocess.check_output(['readelf', '-dW', str(path)], text=True)
        versions = subprocess.check_output(['readelf', '--version-info', str(path)], text=True)
        glibc.update(re.findall(r'Name: (GLIBC_[\d.]+)', versions))
        env = {k: v for k, v in os.environ.items() if not k.startswith('LD_')}
        env['LD_LIBRARY_PATH'] = str(root / 'usr/lib/wine/x86_64-unix')
        resolved = subprocess.run(['ldd', str(path)], env=env, capture_output=True, text=True, check=False)
        modules[str(path.relative_to(root))] = {
            'needed': re.findall(r'\(NEEDED\).*\[(.*?)\]', dynamic),
            'missing': re.findall(r'^\s*(\S+) => not found', resolved.stdout, re.M),
            'loader_status': resolved.returncode,
            'resolution': resolved.stdout.replace(str(root), '@RUNTIME@'),
            'dlopen_candidates': sorted({x.decode() for x in re.findall(rb'\blib[\w+.-]+\.so(?:\.\d+)*\b', path.read_bytes())}),
        }
    return {'scope': 'DT_NEEDED resolution on build host; dlopen names are candidates, not runtime coverage',
            'elf_modules': modules, 'elf_count': len(modules),
            'missing_sonames': sorted({x for m in modules.values() for x in m['missing']}),
            'maximum_glibc_symbol': max(glibc, key=lambda x: tuple(map(int, x[6:].split('.')))),
            'unix_libraries_bundled': False, 'gpu_drivers_bundled': False}


def build(cache, artifacts, output):
    if output.exists() or not output.parent.is_dir():
        raise ValueError('Use a new runtime output with an existing parent')
    for source in (cache, artifacts):
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError('Keep runtime output separate from its inputs')
    lock = json.loads(LOCK.read_text())
    for key in ('archive', 'signature'):
        check_asset(cache / lock[key]['local_name'], lock[key])
    for category in ('patches', 'markers', 'supplements'):
        for name, spec in lock[category].items():
            source = artifacts / safe_name(name)
            if (source.is_symlink() or not source.resolve().is_relative_to(artifacts)
                    or digest_file(source)['sha256'] != spec['sha256']):
                raise ValueError(f'Compatibility artifact changed: {name}')
    space_check(output.parent, 2 * 1024**3)
    output.mkdir(mode=0o700)
    proc = subprocess.Popen(['zstd', '-dc', str(cache / lock['archive']['local_name'])], stdout=subprocess.PIPE)
    try:
        unpack(proc.stdout, output)
    finally:
        proc.stdout.close()
        status = proc.wait()
    if status:
        raise RuntimeError('Wine archive decompression failed')
    info = (output / 'provenance/PKGINFO').read_text()
    for field in ('pkgname = wine', 'pkgver = ' + lock['package_version'], 'arch = x86_64'):
        if field not in info.splitlines():
            raise ValueError('Unexpected Wine package identity')
    base_inventory = inventory(output)
    for category in ('patches', 'markers', 'supplements'):
        for name, spec in lock[category].items():
            target = output / safe_name(spec['target'])
            if category == 'patches':
                if target.is_symlink() or not target.is_file():
                    raise ValueError('Replacement has no matching base component')
            elif target.exists():
                raise ValueError('Unexpected supplementary component collision')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(artifacts / name, target)
            target.chmod(0o755 if spec['target'] == 'usr/bin/wineserver' else 0o644)
            if digest_file(target)['sha256'] != spec['sha256']:
                raise ValueError('Artifact changed during assembly')
    (output / 'licenses').mkdir()
    shutil.copyfile(ROOT / 'packaging/licenses/Wine-LGPL-2.1.txt', output / 'licenses/Wine-LGPL-2.1.txt')
    shutil.copyfile(LOCK, output / 'provenance/wine-runtime.lock.json')
    (output / 'provenance/base-files.json').write_text(json.dumps(base_inventory, indent=2) + '\n')
    audit = audit_unix(output)
    (output / 'provenance/unix-dependencies.json').write_text(json.dumps(audit, indent=2) + '\n')
    report = {'schema': 1, 'kind': 'penguin-private-wine-runtime', 'wine_version': lock['wine_version'],
              'package_version': lock['package_version'], 'lock_sha256': digest_file(LOCK)['sha256'],
              'package_sha256': lock['archive']['sha256'], 'patch_count': len(lock['patches']),
              'supplements': lock['supplements'], 'files': inventory(output),
              'host_wine_required': False, 'host_unix_libraries_required': True,
              'source_release_audit_complete': False}
    (output / 'runtime.json').write_text(json.dumps(report, indent=2) + '\n')
    verify_runtime(output)
    print(json.dumps({'runtime_files': len(report['files']), 'patched_wine_files': report['patch_count'],
                      'elf_modules_audited': audit['elf_count'], 'missing_sonames': audit['missing_sonames'],
                      'maximum_glibc_symbol': audit['maximum_glibc_symbol']}, indent=2))


def archive_runtime(root, output):
    report = verify_runtime(root)
    if output.exists() or output.is_relative_to(root) or not output.parent.is_dir():
        raise ValueError('Archive must be a new file outside the runtime')
    space_check(output.parent, sum(f.get('size', 0) for f in report['files'].values()))
    partial = output.with_name(output.name + '.partial')
    with partial.open('xb') as stream:
        tar = subprocess.Popen(['tar', '--sort=name', '--mtime=@0', '--owner=0', '--group=0',
                                '--numeric-owner', '-C', str(root), '-cf', '-', '.'], stdout=subprocess.PIPE)
        try:
            compressed = subprocess.run(['zstd', '-T2', '-3', '-c'], stdin=tar.stdout, stdout=stream, check=False)
        finally:
            tar.stdout.close()
            status = tar.wait()
    if status or compressed.returncode:
        raise RuntimeError('Runtime archive creation failed; partial retained')
    partial.rename(output)
    print(json.dumps({'archive_bytes': output.stat().st_size, **digest_file(output)}, indent=2))


def restore_runtime(archive, expected_sha256, output):
    if (output.exists() or not output.parent.is_dir() or archive.is_symlink()
            or not re.fullmatch('[0-9a-f]{64}', expected_sha256)
            or digest_file(archive)['sha256'] != expected_sha256):
        raise ValueError('Use a new output and a runtime archive matching its trusted SHA-256')
    space_check(output.parent, 2 * 1024**3)
    output.mkdir(mode=0o700)
    proc = subprocess.Popen(['zstd', '-dc', str(archive)], stdout=subprocess.PIPE)
    try:
        unpack(proc.stdout, output, bundle=True)
    finally:
        proc.stdout.close()
        status = proc.wait()
    if status:
        raise RuntimeError('Runtime archive decompression failed')
    report = verify_runtime(output)
    print(json.dumps({'restored_runtime_files': len(report['files']), 'sha256': expected_sha256}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    fetch = sub.add_parser('fetch'); fetch.add_argument('--cache', required=True, type=Path)
    make = sub.add_parser('build')
    make.add_argument('--cache', required=True, type=Path)
    make.add_argument('--artifacts', required=True, type=Path)
    make.add_argument('--output', required=True, type=Path)
    verify = sub.add_parser('verify'); verify.add_argument('--runtime', required=True, type=Path)
    archive = sub.add_parser('archive')
    archive.add_argument('--runtime', required=True, type=Path)
    archive.add_argument('--output', required=True, type=Path)
    restore = sub.add_parser('restore')
    restore.add_argument('--archive', required=True, type=Path)
    restore.add_argument('--sha256', required=True)
    restore.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'fetch':
        cache = external_path(args.cache); cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        with work_lock(cache):
            lock = json.loads(LOCK.read_text())
            for name in ('archive', 'signature'):
                download_asset(cache, lock[name], validate=official_url)
    elif args.action == 'build':
        cache = external_path(args.cache)
        with work_lock(cache):
            build(cache, args.artifacts.resolve(strict=True), external_path(args.output))
    elif args.action == 'archive':
        archive_runtime(external_path(args.runtime), external_path(args.output))
    elif args.action == 'restore':
        restore_runtime(args.archive.resolve(strict=True), args.sha256, external_path(args.output))
    else:
        report = verify_runtime(external_path(args.runtime))
        print(json.dumps({'verified_runtime_files': len(report['files']), 'host_wine_required': False}, indent=2))


if __name__ == '__main__':
    os.umask(0o077)
    main()
