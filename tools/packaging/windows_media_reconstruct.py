#!/usr/bin/env python3
"""Pinned two-DLL acquisition recipe; invoked by windows_media_acquire.py.

No VM is booted. A read-only tool container extracts installation media; a fresh
headless Wine prefix runs the verified Microsoft delta engine plus our decoder
bridge. This is a private reproduction experiment, not a runtime installer.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.parse
import xml.etree.ElementTree as ET

from windows_media_acquire import (ROOT, LOCK_PATH, check_asset, digest_file,
                                  download_asset, space_check, verify_dlls)


def tool_source_url(url):
    u = urllib.parse.urlsplit(url)
    if (u.scheme != 'https' or u.netloc != 'codeload.github.com'
            or not u.path.startswith('/coderforlife/ms-compress/tar.gz/') or u.query):
        raise ValueError('Unexpected decoder source URL')
    return url


def run(command, **kwargs):
    return subprocess.run([str(v) for v in command], check=True, timeout=900, **kwargs)


def container(image, binds, command, stdout=None):
    if os.environ.get('PENGUIN_TOOLCHAIN') == '1':
        # The outer private SDK already isolated these tools and files.
        def translate(value):
            value = str(value)
            for source, dest, _ in sorted(binds, key=lambda b: -len(b[1])):
                value = value.replace(dest, str(source))
            return value
        return run([translate(v) for v in command], stdout=stdout)
    args = ['sudo', '-n', 'docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
            '--user', f'{os.getuid()}:{os.getgid()}', '--memory', '2g', '--cpus', '2',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only']
    for source, dest, readonly in binds:
        if ',' in str(source):
            raise ValueError('Docker bind paths cannot contain commas')
        args += ['--mount', f'type=bind,src={source},dst={dest}' + (',readonly' if readonly else '')]
    args += ['--entrypoint', command[0], image, *command[1:]]
    run(args, stdout=stdout)


def read_resource(source, output, metadata):
    """Read a bounded, pinned raw resource from an already verified MSU."""
    offset, size = metadata['offset'], metadata['size']
    if offset < 0 or size <= 0 or offset + size > source.stat().st_size:
        raise ValueError('Resource is outside package bounds')
    space_check(output.parent, size)
    with source.open('rb') as src, output.open('xb') as dst:
        src.seek(offset)
        remaining = size
        while remaining:
            data = src.read(min(4 * 1024**2, remaining))
            if not data:
                raise ValueError('Truncated resource')
            space_check(output.parent, remaining)
            dst.write(data)
            remaining -= len(data)
    check_asset(output, metadata)


def select_forward_deltas(cix, expected, psf_size):
    """Require the exact AMD64 component paths, hashes and RAW PSF ranges."""
    ns = '{urn:ContainerIndex}'
    paths = {v['path']: name for name, v in expected.items()}
    found = {}
    for _, element in ET.iterparse(cix, events=['end']):
        if element.tag != ns + 'File':
            continue
        name = paths.get(element.get('name'))
        if name:
            spec = expected[name]
            src = element.find(ns + 'Delta/' + ns + 'Source')
            if src is None:
                raise ValueError('Missing delta source')
            digest = src.find(ns + 'Hash')
            if (name in found or src.get('type') != 'RAW' or digest is None
                    or digest.get('alg') != 'SHA256' or digest.get('value', '').lower() != spec['sha256']
                    or int(src.get('offset', -1)) != spec['offset']
                    or int(src.get('length', -1)) != spec['size']
                    or spec['offset'] < 0 or spec['size'] <= 0
                    or spec['offset'] + spec['size'] > psf_size):
                raise ValueError(f'Delta identity/range mismatch: {name}')
            found[name] = spec
        element.clear()
    if found.keys() != expected.keys():
        raise ValueError('Missing target delta')
    return found


def unpack_decoder(archive, destination, commit):
    prefix = f'ms-compress-{commit}/'
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        if sum(m.size for m in members) > 16 * 1024**2:
            raise ValueError('Unexpected decoder source expansion')
        for member in members:
            if member.name == prefix.rstrip('/') and member.isdir():
                continue
            if (not member.name.startswith(prefix) or '..' in Path(member.name).parts
                    or not (member.isfile() or member.isdir())):
                raise ValueError('Unsafe source archive member')
        tar.extractall(destination, members=members, filter='data')
    return destination / prefix


def reconstruct(work, output, control, lock, wine):
    if os.geteuid() == 0:
        raise ValueError('Run as a normal user; only the extraction container needs sudo')
    if (output == control or output.is_relative_to(control) or control.is_relative_to(output)
            or work.is_relative_to(output) or work.is_relative_to(control)):
        raise ValueError('Use distinct cache, output and control directories')
    for path in (output, control):
        if path.exists():
            raise ValueError(f'Reconstruction requires a new directory: {path}')
        if not path.parent.is_dir():
            raise ValueError(f'Create the parent directory first: {path.parent}')
        space_check(path.parent, 2 * 1024**3)
    for executable in (wine, 'x86_64-w64-mingw32-gcc', 'x86_64-w64-mingw32-g++', *([] if os.environ.get('PENGUIN_TOOLCHAIN') == '1' else ['sudo'])):
        if not shutil.which(executable):
            raise ValueError(f'Required existing tool is missing: {executable}')
    image = lock['extraction_tools']['wimlib_linux_container']
    if os.environ.get('PENGUIN_TOOLCHAIN') != '1':
        run(['sudo', '-n', 'docker', 'image', 'inspect', image], stdout=subprocess.DEVNULL)
    print('Verifying complete official media before extracting any executable...', flush=True)
    for key in ('package', 'base_media'):
        check_asset(work / lock[key]['local_name'], lock[key])
    source_spec = lock['extraction_tools']['ms_compress']
    download_asset(work, source_spec, validate=tool_source_url)
    output.mkdir(mode=0o700)
    control.mkdir(mode=0o700)
    bins = control / 'bin'
    bins.mkdir()
    base = output / 'base'
    base.mkdir()
    recipe = lock['recipe']
    esd = work / lock['base_media']['local_name']
    msu = work / lock['package']['local_name']
    paths = [part['path'] for specs in recipe['base_files'].values() for part in specs.values()]
    paths += ['/Windows/System32/cabinet.dll']
    print('Extracting base files and reverse deltas from the official ESD...', flush=True)
    container(image, [(esd, '/base.esd', True), (base, '/out', False)],
              ['wimlib-imagex', 'extract', '/base.esd', str(lock['base_media']['image_index']),
               *paths, '--dest-dir=/out', '--no-acls', '--no-attributes', '--preserve-dir-structure'])
    for specs in recipe['base_files'].values():
        for spec in specs.values():
            check_asset(base / spec['path'].lstrip('/'), spec)
    cabinet = base / 'Windows/System32/cabinet.dll'
    check_asset(cabinet, lock['extraction_tools']['cabinet.dll'])
    shutil.copyfile(cabinet, bins / 'cabinet.dll')
    print('Reading bounded update resources and verifying forward deltas...', flush=True)
    for key in ('desktop_deployment', 'inner_wim'):
        spec = recipe['msu_resources'][key]
        read_resource(msu, output / spec['local_name'], spec)
    container(image, [(output, '/work', False)],
              ['wimlib-imagex', 'extract', '/work/update.wim', '1', '/express.psf.cix.xml',
               '--dest-dir=/work', '--no-acls', '--no-attributes'])
    check_asset(output / 'express.psf.cix.xml', recipe['cix'])
    psf = recipe['msu_resources']['psf']
    for name, spec in select_forward_deltas(output / 'express.psf.cix.xml', recipe['forward'], psf['size']).items():
        read_resource(msu, output / (name + '.delta'), {**spec, 'offset': psf['offset'] + spec['offset']})
    with (bins / 'UpdateCompression.dll').open('xb') as dll:
        container(image, [(output / 'DesktopDeployment.cab', '/input.cab', True)],
                  ['cabextract', '-p', '-F', 'UpdateCompression.dll', '/input.cab'], stdout=dll)
    check_asset(bins / 'UpdateCompression.dll', lock['extraction_tools']['UpdateCompression.dll'])
    print('Building the acquisition helper and pinned open-source decoder...', flush=True)
    source = unpack_decoder(work / source_spec['local_name'], control, source_spec['commit'])
    run(['x86_64-w64-mingw32-g++', '-shared', '-O2', '-Wall', '-DMSCOMP_API_EXPORT',
         '-DMSCOMP_API_DLL', '-DMSCOMP_WITHOUT_LARGE_STACK', '-fno-exceptions', '-fno-rtti',
         '-static-libgcc', '-static-libstdc++', source / 'src/xpress_huff_decompress.cpp',
         '-o', bins / 'mscompression.dll'])
    run(['x86_64-w64-mingw32-gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-municode',
         '-static-libgcc', ROOT / 'tools/packaging/apply_windows_delta.c',
         '-o', bins / 'apply_windows_delta.exe'])
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('WINE', 'WINEDLL', 'LD_')) and k not in ('DBUS_SESSION_BUS_ADDRESS', 'XAUTHORITY')}
    env.update(WINEPREFIX=str(control / 'prefix'), WINEARCH='win64', WINEDEBUG='-all',
               WINEDLLOVERRIDES='winemenubuilder.exe=d;mscoree,mshtml=;cabinet=n',
               DISPLAY='', WAYLAND_DISPLAY='')
    wine = str(Path(shutil.which(wine)).resolve())
    wine_version = subprocess.check_output([wine, '--version'], env=env, text=True).strip()
    final = output / 'dlls'
    final.mkdir()
    win = lambda p: 'Z:' + str(p).replace('/', '\\')
    for name, specs in recipe['base_files'].items():
        space_check(output, 32 * 1024**2)
        space_check(control, 1024**3)
        print(f'Reconstructing {name}: media → checkpoint → target...', flush=True)
        checkpoint = output / (name + '.checkpoint')
        for src, delta, dst in ((base / specs['file']['path'].lstrip('/'),
                                 base / specs['reverse']['path'].lstrip('/'), checkpoint),
                                (checkpoint, output / (name + '.delta'), final / name)):
            run([wine, bins / 'apply_windows_delta.exe', win(bins / 'UpdateCompression.dll'),
                 win(src), win(delta), win(dst)], env=env, cwd=bins)
            check_asset(dst, recipe['checkpoint'][name] if dst == checkpoint else lock['files'][name])
    report = verify_dlls(final, lock)
    report.update(method='official ESD + reverse delta + official cumulative-update forward delta',
                  generated_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  wine_version=wine_version, media={k: lock[k] for k in ('package', 'base_media')},
                  recipe_lock_sha256=digest_file(LOCK_PATH)['sha256'],
                  helper_source_sha256={p.name: digest_file(p)['sha256'] for p in
                                        (ROOT / 'tools/packaging/apply_windows_delta.c',
                                         ROOT / 'tools/packaging/xpress_acquisition_bridge.h')},
                  windows_vm_booted=False, symbol_server_used_for_reconstruction=False,
                  existing_runtime_or_prefix_used=False)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
