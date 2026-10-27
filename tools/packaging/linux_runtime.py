#!/usr/bin/env python3
"""Pinned Linux userspace and isolated dependency audit for the private Wine.

This development layer uses Valve's pressure-vessel without Steam. It does not
install packages or connect to the live desktop, network or physical devices.
An audit is not qualification of graphics, hardware or a new distribution.
"""
import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import urllib.parse

from windows_media_acquire import (ROOT, check_asset, digest_file, download_asset,
                                  external_path, space_check, work_lock)
from wine_runtime import verify_runtime, runtime_environment

LOCK = ROOT / 'packaging/linux-runtime.lock.json'
DEPOT = 'SteamLinuxRuntime_4'


def official_url(url):
    u = urllib.parse.urlsplit(url)
    if (u.scheme != 'https' or u.netloc != 'repo.steampowered.com' or u.query or u.fragment
            or not re.fullmatch(r'/steamrt4/images/4\.0\.\d{8}\.\d+/SteamLinuxRuntime_4\.tar\.xz', u.path)):
        raise ValueError('Expected a versioned official Steam Runtime archive')
    return url


def support_url(url):
    u = urllib.parse.urlsplit(url)
    if (u.scheme != 'https' or u.netloc != 'deb.debian.org' or u.query or u.fragment
            or not re.fullmatch(r'/debian(?:-security)?/pool/(?:updates/)?main/[a-z0-9][a-z0-9+./_%~:-]*_(?:amd64|all)\.deb', u.path)
            or '..' in urllib.parse.unquote(u.path).split('/')):
        raise ValueError('Expected an official pinned Debian package')
    return url


def extract_support(archive, output):
    """Read a pinned Debian data archive; never execute maintainer scripts."""
    data = archive.read_bytes()
    if not data.startswith(b'!<arch>\n') or len(data) > 128 * 1024**2:
        raise ValueError('Invalid support package')
    offset, payloads = 8, []
    while offset < len(data):
        header = data[offset:offset + 60]
        if len(header) != 60 or header[58:] != b'`\n':
            raise ValueError('Invalid ar header')
        size = int(header[48:58])
        if size < 0 or offset + 60 + size > len(data):
            raise ValueError('Invalid ar size')
        if header[:16].strip().rstrip(b'/').startswith(b'data.tar'):
            payloads.append(data[offset + 60:offset + 60 + size])
        offset += 60 + size + size % 2
    if len(payloads) != 1:
        raise ValueError('Expected exactly one Debian data archive')
    with tarfile.open(fileobj=io.BytesIO(payloads[0]), mode='r:*') as tar:
        members, names, total = tar.getmembers(), set(), 0
        for m in members:
            p = PurePosixPath(m.name)
            if (p.is_absolute() or '..' in p.parts or str(p) in names or '\\' in m.name
                    or not (m.isdir() or m.isfile() or m.issym()) or m.mode & 0o6000):
                raise ValueError('Unsafe support package member')
            names.add(str(p)); total += m.size
            if m.issym() and (Path(m.linkname).is_absolute() or
                             not (output / str(p)).parent.joinpath(m.linkname).resolve().is_relative_to(output)):
                raise ValueError('Escaping support package link')
        if total > 512 * 1024**2 or len(members) > 20000:
            raise ValueError('Unexpected support package size')
        tar.extractall(output, members=members, filter='data')


def platform(root):
    lock = json.loads(LOCK.read_text())
    return root / DEPOT / ('steamrt4_platform_' + lock['build_id'])


def tree(root):
    files = {}
    for p in sorted((root / DEPOT).rglob('*')):
        # pressure-vessel uses this empty file as a runtime reference lock.
        if p == platform(root) / 'files/.ref':
            if not p.is_file() or p.is_symlink() or p.stat().st_size:
                raise ValueError('Invalid runtime reference lock')
            continue
        name = str(p.relative_to(root))
        if p.is_symlink():
            if not p.resolve().is_relative_to(root / DEPOT):
                raise ValueError('External Linux-runtime link')
            files[name] = {'link': os.readlink(p)}
        elif p.is_file():
            files[name] = {**digest_file(p), 'size': p.stat().st_size, 'mode': p.stat().st_mode & 0o777}
        elif not p.is_dir():
            raise ValueError('Nonregular Linux-runtime entry')
    return files


def extract(archive, output):
    with tarfile.open(archive, 'r:xz') as tar:
        members = tar.getmembers()
        names = set()
        total = 0
        for m in members:
            p = PurePosixPath(m.name)
            if (p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] != DEPOT
                    or '\\' in m.name or str(p) in names):
                raise ValueError('Invalid or duplicate Linux-runtime member')
            names.add(str(p))
            if not (m.isdir() or m.isfile() or m.issym()) or m.mode & 0o6000:
                raise ValueError('Unsupported Linux-runtime entry')
            if m.issym() and (Path(m.linkname).is_absolute() or
                             not (output / m.name).parent.joinpath(m.linkname).resolve().is_relative_to(output / DEPOT)):
                raise ValueError('Escaping Linux-runtime symlink')
            total += m.size
        if total > 4 * 1024**3 or len(members) > 100000:
            raise ValueError('Unexpected Linux-runtime extraction size')
        space_check(output, total)
        tar.extractall(output, members=members, filter='data')


def prepare(cache, output):
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError('Use a new Linux-runtime directory with an existing parent')
    if output.is_relative_to(cache) or cache.is_relative_to(output):
        raise ValueError('Keep Linux runtime separate from downloads')
    lock = json.loads(LOCK.read_text())
    archive = cache / lock['archive']['local_name']
    check_asset(archive, lock['archive'])
    for item in lock['support_packages']:
        check_asset(cache / item['local_name'], item)
    space_check(output.parent, 2 * 1024**3)
    output.mkdir(mode=0o700)
    extract(archive, output)
    for item in lock['support_packages']:
        extract_support(cache / item['local_name'], output / DEPOT / 'penguin-libraries')
    (platform(output) / 'files/.ref').touch()
    report = {'schema': 1, 'kind': 'penguin-linux-runtime',
              'lock_sha256': digest_file(LOCK)['sha256'], 'files': tree(output)}
    (output / 'linux-runtime.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'files': len(report['files']), 'build_id': lock['build_id'], 'output': str(output)}))


def verify(root):
    report = json.loads((root / 'linux-runtime.json').read_text())
    if (report.get('schema') != 1 or report.get('kind') != 'penguin-linux-runtime'
            or report.get('lock_sha256') != digest_file(LOCK)['sha256'] or report['files'] != tree(root)):
        raise ValueError('Linux-runtime identity or inventory changed')
    return report


def clean_environment():
    return {'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()),
            'USER': os.environ.get('USER', str(os.getuid())),
            'LOGNAME': os.environ.get('USER', str(os.getuid())), 'LANG': 'C.UTF-8',
            'PRESSURE_VESSEL_ARCHITECTURES': 'x86_64-linux-gnu'}


def container_command(root, output, command, read_only_paths=()):
    # Runtime and variable directory share one bind mount, so pressure-vessel
    # can hardlink its private copy instead of copying every file across mounts.
    mounts = [f'--filesystem={p}:ro' for p in (ROOT, *read_only_paths)]
    return [str(root / DEPOT / 'pressure-vessel/bin/pressure-vessel-wrap'),
            '--batch', '--no-systemd-scope', '--no-generate-locales', '--no-gc-runtimes',
            '--no-import-vulkan-layers', '--no-import-openxr-1-runtimes',
            '--graphics-provider=', '--copy-runtime', '--runtime=' + str(platform(root)),
            '--variable-dir=' + str(root / 'var'), '--home=' + str(output / 'home'),
            '--unshare-pid', '--terminate-timeout=3', *mounts,
            f'--filesystem={output}:rw', '--', *map(str, command)]


def isolated_command(root, output, command, freeze_app=False, read_only_paths=()):
    from prepare_clean_prefix import sandbox
    args = sandbox(output, container_command(root, output, command, read_only_paths), hide_host_wine=True,
                   freeze_app=freeze_app)
    boundary = args.index('--')
    args[boundary:boundary] = ['--bind', str(root), str(root)]
    # A read-only socket still permits IPC. This headless audit must not talk
    # to the host system bus (the session bus is already hidden by sandbox()).
    if Path('/run/dbus').is_dir():
        args[boundary:boundary] = ['--tmpfs', '/run/dbus']
    return args


def classify_ldd(text, status, needed):
    missing = sorted(set(re.findall(r'^\s*(\S+) => not found', text, re.M)))
    versions = sorted(set(re.findall(r'version [^\n]+not found[^\n]*', text)))
    static = not needed and ('not a dynamic executable' in text or 'statically linked' in text)
    return {'missing': missing, 'version_errors': versions, 'loader_status': status,
            'linked': not missing and not versions and (status == 0 or static)}


def library_path(root, wine):
    libs = root / DEPOT / 'penguin-libraries/usr/lib/x86_64-linux-gnu'
    return ':'.join(map(str, (wine / 'usr/lib/wine/x86_64-unix', libs, libs / 'samba')))


def worker(wine, output, root):
    identity = Path('/etc/os-release').read_text()
    expected = json.loads(LOCK.read_text())['build_id']
    if f'BUILD_ID="{expected}"' not in identity:
        raise RuntimeError('Dependency audit is not inside the pinned Linux userspace')
    baseline = json.loads((wine / 'provenance/unix-dependencies.json').read_text())
    env = dict(os.environ, LD_LIBRARY_PATH=library_path(root, wine))
    modules = {}
    for name, item in baseline['elf_modules'].items():
        proc = subprocess.run(['/usr/bin/ldd', str(wine / name)], env=env,
                              capture_output=True, text=True, timeout=15)
        text = proc.stdout + proc.stderr
        modules[name] = {**classify_ldd(text, proc.returncode, item['needed']),
                         'resolution': text.replace(str(wine), '@WINE@').replace(str(root), '@LINUX@')}
    dynamic = {}
    for name in sorted({n for m in baseline['elf_modules'].values() for n in m['dlopen_candidates']}):
        proc = subprocess.run([sys.executable, '-c',
                               'import ctypes,sys; ctypes.CDLL(sys.argv[1])', name],
                              env=env, capture_output=True, text=True, timeout=15)
        dynamic[name] = {'loadable': proc.returncode == 0,
                         'error': proc.stderr.strip() if proc.returncode else ''}
    # Execute a real PE command in a new disposable prefix, not just --version.
    smoke_env = runtime_environment(wine, output / 'prefix', env)
    smoke_env['LD_LIBRARY_PATH'] = env['LD_LIBRARY_PATH']
    smoke_env.update(WINEDEBUG='-all', WINEDLLOVERRIDES='winemenubuilder.exe=d')
    smoke_env.pop('DISPLAY', None)
    smoke_env.pop('WAYLAND_DISPLAY', None)
    try:
        smoke = subprocess.run([str(wine / 'usr/bin/wine'), 'cmd', '/c', 'echo PENGUIN_LINUX_RUNTIME_OK'],
                               env=smoke_env, capture_output=True, text=True, timeout=60)
        execution = {'exit_code': smoke.returncode,
                     'passed': smoke.returncode == 0 and 'PENGUIN_LINUX_RUNTIME_OK' in smoke.stdout,
                     'stdout': smoke.stdout.strip(),
                     'stderr': smoke.stderr.replace(str(output), '@OUTPUT@').replace(str(wine), '@WINE@').strip()}
    except subprocess.TimeoutExpired:
        execution = {'passed': False, 'error': 'PE command exceeded 60 seconds'}
    finally:
        subprocess.run([str(wine / 'usr/bin/wineserver'), '-k'], env=smoke_env,
                       capture_output=True, timeout=10)
    report = {'schema': 1, 'linux_runtime_build': expected,
              'libc': subprocess.check_output(['getconf', 'GNU_LIBC_VERSION'], text=True).strip(),
              'scope': 'Isolated native loader, dlopen and disposable-prefix PE command checks; no UI, GPU context or devices',
              'live_desktop_used': False, 'physical_devices_exposed': False, 'host_graphics_imported': False,
              'host_system_bus_exposed': False,
              'modules_total': len(modules), 'modules_linked': sum(x['linked'] for x in modules.values()),
              'dlopen_total': len(dynamic), 'dlopen_loadable': sum(x['loadable'] for x in dynamic.values()),
              'pe_command': execution,
              'modules': modules, 'dlopen': dynamic, 'end_to_end_qualified': False}
    (output / 'linux-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def audit(root, wine, output):
    verify(root)
    verify_runtime(wine)
    if (output.exists() or output.is_symlink() or not output.parent.is_dir()
            or any(output.is_relative_to(p) or p.is_relative_to(output) for p in (root, wine, ROOT))):
        raise ValueError('Use a new separate audit directory')
    space_check(output.parent, 2 * 1024**3)
    output.mkdir(mode=0o700)
    (output / 'home').mkdir()
    with work_lock(root):
        command = isolated_command(root, output, ['/usr/bin/python3', str(Path(__file__).resolve()),
                                  'worker', '--wine', str(wine), '--output', str(output),
                                  '--linux-runtime', str(root)], read_only_paths=(wine, root))
        with (output / 'linux-audit.log').open('xb') as log:
            subprocess.run(command, env=clean_environment(), stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=180)
    verify(root)
    report = json.loads((output / 'linux-audit.json').read_text())
    print(json.dumps({k: v for k, v in report.items() if k not in ('modules', 'dlopen')}, indent=2))
    return 0 if (report['modules_linked'] == report['modules_total']
                 and report['dlopen_loadable'] == report['dlopen_total']
                 and report['pe_command']['passed']) else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('fetch', 'prepare', 'verify', 'audit', 'worker'))
    p.add_argument('--cache', type=Path)
    p.add_argument('--linux-runtime', type=Path)
    p.add_argument('--wine', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    required = {'fetch': ('cache',), 'prepare': ('cache', 'output'), 'verify': ('linux_runtime',),
                'audit': ('linux_runtime', 'wine', 'output'), 'worker': ('linux_runtime', 'wine', 'output')}
    if any(getattr(a, name) is None for name in required[a.action]):
        p.error('Missing required paths for ' + a.action)
    if a.action == 'worker':
        worker(a.wine, a.output, a.linux_runtime)
        return 0
    for name in required[a.action]:
        setattr(a, name, external_path(getattr(a, name)))
    if a.action == 'fetch':
        a.cache.mkdir(mode=0o700, exist_ok=True)
        with work_lock(a.cache):
            download_asset(a.cache, json.loads(LOCK.read_text())['archive'], validate=official_url)
            for item in json.loads(LOCK.read_text())['support_packages']:
                download_asset(a.cache, item, validate=support_url)
    elif a.action == 'prepare':
        prepare(a.cache, a.output)
    elif a.action == 'verify':
        print(json.dumps({'files_verified': len(verify(a.linux_runtime)['files'])}))
    else:
        return audit(a.linux_runtime, a.wine, a.output)
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(main())
