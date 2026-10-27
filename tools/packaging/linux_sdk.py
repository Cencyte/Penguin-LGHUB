#!/usr/bin/env python3
"""Build selected Wine Unix modules in a pinned private SDK, without host installs.

The SDK is an ordinary directory used as a bubblewrap root. Package maintainer
scripts are never executed. The resulting Wine retains all pinned compatibility
artifacts; only the explicitly listed upstream Unix modules are replaced.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile

from windows_media_acquire import (ROOT, check_asset, digest_file, download_asset,
                                  external_path, space_check, work_lock)
from linux_runtime import extract_support, support_url
from wine_runtime import verify_runtime, audit_unix, inventory

LOCK = ROOT / 'packaging/linux-sdk.lock.json'
TARGETS = ('dlls/winedmo/winedmo.so', 'dlls/wpcap/wpcap.so')


def pinned_url(url):
    lock = json.loads(LOCK.read_text())
    if url not in (lock['archive']['url'], lock['wine_source']['url']):
        raise ValueError('Unexpected SDK/source URL')
    return url


def extract_root(archive, output, limit=5 * 1024**3):
    """Preserve guest-root absolute link semantics without escaping extraction."""
    if output.exists():
        raise ValueError('SDK/source extraction requires a new directory')
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if len(members) > 150000 or sum(m.size for m in members) > limit:
            raise ValueError('Unexpected SDK/source extraction size')
        names = set()
        for m in members:
            p = PurePosixPath(m.name)
            if p.is_absolute() or '..' in p.parts or str(p) in names:
                raise ValueError('Unsafe or duplicate SDK path')
            names.add(str(p))
            if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
                raise ValueError('Special file in SDK/source archive')
        space_check(output.parent, sum(m.size for m in members))
        output.mkdir(mode=0o700)
        def contained(member, destination):
            if member.issym() and member.linkname.startswith('/'):
                member = member.replace(linkname=os.path.relpath(member.linkname,
                                           '/' + str(PurePosixPath(member.name).parent)))
            return tarfile.data_filter(member, destination)
        tar.extractall(output, members=members, filter=contained)


def sdk_command(root, work, command):
    # Everything outside the sysroot/work directories is absent, including the
    # host desktop, physical devices, system Wine and network. No root needed.
    return ['bwrap', '--unshare-all', '--die-with-parent', '--ro-bind', str(root), '/',
            '--bind', str(work), '/work', '--proc', '/proc', '--dev', '/dev',
            '--tmpfs', '/tmp', '--tmpfs', '/run', '--clearenv',
            '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/tmp',
            '--setenv', 'LC_ALL', 'C.UTF-8', '--setenv', 'SOURCE_DATE_EPOCH', '1761868800',
            '--setenv', 'LD_LIBRARY_PATH', '/usr/lib/x86_64-linux-gnu',
            '--chdir', '/work/build', '--', *command]


def fetch(cache, support_cache):
    lock = json.loads(LOCK.read_text())
    cache.mkdir(exist_ok=True, mode=0o700)
    support_cache.mkdir(exist_ok=True, mode=0o700)
    with work_lock(cache):
        for item in (lock['archive'], lock['wine_source']):
            download_asset(cache, item, validate=pinned_url)
    with work_lock(support_cache):
        for item in lock['build_packages']:
            download_asset(support_cache, item, validate=support_url)


def build(cache, support_cache, output, jobs):
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or not 1 <= jobs <= 16:
        raise ValueError('Use a new SDK build directory and 1–16 workers')
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (cache, support_cache, ROOT)):
        raise ValueError('Keep SDK work separate from caches and source checkout')
    lock = json.loads(LOCK.read_text())
    if tuple(lock['targets']) != TARGETS:
        raise ValueError('SDK target allowlist changed')
    for item in (lock['archive'], lock['wine_source']):
        check_asset(cache / item['local_name'], item)
    for item in lock['build_packages']:
        check_asset(support_cache / item['local_name'], item)
    space_check(output.parent, 7 * 1024**3)
    output.mkdir(mode=0o700)
    sdk, work = output / 'sysroot', output / 'work'
    work.mkdir()
    extract_root(cache / lock['archive']['local_name'], sdk)
    for item in lock['build_packages']:
        extract_support(support_cache / item['local_name'], sdk)
    extract_root(cache / lock['wine_source']['local_name'], work / 'source', 512 * 1024**2)
    if (work / 'source/wine-wine-10.20/VERSION').read_text().strip() != 'Wine version 10.20':
        raise ValueError('Wrong Wine source')
    (work / 'build').mkdir()
    compile_modules(output, jobs)


def compile_modules(output, jobs):
    lock = json.loads(LOCK.read_text())
    sdk, work = output / 'sysroot', output / 'work'
    # A read-only root cannot create new mountpoints during bwrap setup.
    for directory in ('work', 'proc', 'dev', 'tmp', 'run'):
        (sdk / directory).mkdir(exist_ok=True)
    for name, command in (
        ('configure', ['/work/source/wine-wine-10.20/configure', *lock['configure']]),
        ('build', ['make', '-j' + str(jobs), *TARGETS]),
    ):
        with (output / (name + '.log')).open('xb') as log:
            subprocess.run(sdk_command(sdk, work, command), check=True, stdout=log,
                           stderr=subprocess.STDOUT, timeout=900)
    modules = {name: digest_file(work / 'build' / name) for name in TARGETS}
    report = {'schema': 1, 'lock_sha256': digest_file(LOCK)['sha256'], 'modules': modules,
              'host_packages_modified': False, 'network_used_during_build': False}
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


def derive(base, built, output):
    report = verify_runtime(base)
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError('Use a new Wine directory')
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (base, built, ROOT)):
        raise ValueError('Derived runtime must be separate from inputs')
    build_report = json.loads((built / 'build.json').read_text())
    if (build_report.get('schema') != 1 or build_report.get('lock_sha256') != digest_file(LOCK)['sha256']
            or set(build_report['modules']) != set(TARGETS)):
        raise ValueError('Build provenance does not match the SDK lock')
    for name, digest in build_report['modules'].items():
        if digest_file(built / 'work/build' / name) != digest:
            raise ValueError('Built module changed')
    space_check(output.parent, sum(x.get('size', 0) for x in report['files'].values()))
    # Reflink is an optimization only; ordinary copies remain valid. Never use
    # hardlinks: replacing a module must not mutate the regression baseline.
    subprocess.run(['cp', '-a', '--reflink=auto', str(base), str(output)], check=True)
    for name in TARGETS:
        shutil.copy2(built / 'work/build' / name, output / 'usr/lib/wine/x86_64-unix' / Path(name).name)
    shutil.copyfile(built / 'build.json', output / 'provenance/linux-sdk-build.json')
    shutil.copyfile(LOCK, output / 'provenance/linux-sdk.lock.json')
    audit = audit_unix(output)
    (output / 'provenance/unix-dependencies.json').write_text(json.dumps(audit, indent=2) + '\n')
    report.update(files=inventory(output), derived_from=digest_file(base / 'runtime.json'),
                  linux_sdk_modules=build_report['modules'])
    (output / 'runtime.json').write_text(json.dumps(report, indent=2) + '\n')
    verify_runtime(output)  # Includes every original pinned compatibility file.
    print(json.dumps({'derived_runtime': str(output), 'rebuilt_unix_modules': len(TARGETS),
                      'all_pinned_compatibility_artifacts_preserved': True}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('fetch', 'build', 'derive'))
    for key in ('cache', 'support-cache', 'output', 'base', 'built'):
        p.add_argument('--' + key, type=Path)
    p.add_argument('--jobs', type=int, default=4)
    a = p.parse_args()
    required = {'fetch': ('cache', 'support_cache'), 'build': ('cache', 'support_cache', 'output'),
                'derive': ('base', 'built', 'output')}[a.action]
    for name in required:
        if getattr(a, name) is None:
            p.error('Missing --' + name.replace('_', '-'))
        setattr(a, name, external_path(getattr(a, name)))
    if a.action == 'fetch':
        fetch(a.cache, a.support_cache)
    elif a.action == 'build':
        build(a.cache, a.support_cache, a.output, a.jobs)
    else:
        derive(a.base, a.built, a.output)


if __name__ == '__main__':
    os.umask(0o077)
    main()
