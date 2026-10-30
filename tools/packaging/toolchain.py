#!/usr/bin/env python3
"""Pinned private acquisition SDK, executable with bubblewrap or rootless Podman."""
import argparse
import io
import tarfile
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from windows_media_acquire import ROOT, check_asset, digest_file, download_asset, external_path, work_lock
import linux_sdk
import linux_runtime
import wine_runtime

LOCK = ROOT / 'packaging/toolchain.lock.json'


def identities():
    return {p.name: digest_file(p)['sha256'] for p in (LOCK, linux_sdk.LOCK, wine_runtime.LOCK)}


def fetch(cache):
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    with work_lock(cache):
        sdk = json.loads(linux_sdk.LOCK.read_text())
        wine = json.loads(wine_runtime.LOCK.read_text())
        download_asset(cache, sdk['archive'], validate=linux_sdk.pinned_url)
        download_asset(cache, wine['archive'], validate=wine_runtime.official_url)
        for p in [*sdk['build_packages'], *json.loads(LOCK.read_text())['packages']]:
            download_asset(cache, p, validate=linux_runtime.support_url)


def extract_tools(archive, output):
    # Compiler packages use hardlinks between executable aliases. Accept these
    # only inside the private root; tar's data filter also checks link targets.
    data = linux_runtime.debian_payload(archive)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = tar.getmembers()
        if len(members) > 40000 or sum(m.size for m in members) > 1024**3:
            raise ValueError('Tool package expansion limit')
        for m in members:
            if (Path(m.name).is_absolute() or '..' in Path(m.name).parts or
                    not (m.isfile() or m.isdir() or m.issym() or m.islnk()) or m.mode & 0o6000):
                raise ValueError('Unsafe tool package member')
        tar.extractall(output, members=members, filter='data')


def prepare(cache, output):
    if output.exists() or output.is_symlink():
        raise ValueError('Toolchain output must be a new directory')
    sdk = json.loads(linux_sdk.LOCK.read_text())
    wine = json.loads(wine_runtime.LOCK.read_text())
    for p in [sdk['archive'], wine['archive'], *sdk['build_packages'], *json.loads(LOCK.read_text())['packages']]:
        check_asset(cache / p['local_name'], p)
    linux_sdk.extract_root(cache / sdk['archive']['local_name'], output)
    for p in [*sdk['build_packages'], *json.loads(LOCK.read_text())['packages']]:
        extract_tools(cache / p['local_name'], output)
    private_wine = output / 'opt/wine'
    private_wine.mkdir(parents=True)
    proc = subprocess.Popen(['zstd', '-dc', str(cache / wine['archive']['local_name'])], stdout=subprocess.PIPE)
    try:
        wine_runtime.unpack(proc.stdout, private_wine)
    finally:
        proc.stdout.close()
        if proc.wait():
            raise RuntimeError('Wine decompression failed')
    # Debian alternatives are maintainer scripts; create only our two private links.
    for name in ('gcc', 'g++'):
        link = output / ('usr/bin/x86_64-w64-mingw32-' + name)
        if not link.exists():
            link.symlink_to('x86_64-w64-mingw32-' + name + '-posix')
    for name in ('work', 'project', 'proc', 'dev', 'tmp', 'run'):
        (output / name).mkdir(exist_ok=True)
    (output / 'toolchain.json').write_text(json.dumps({'schema':1,'locks':identities()},indent=2)+'\n')


def verify(root):
    data = json.loads((root / 'toolchain.json').read_text())
    if data.get('locks') != identities():
        raise ValueError('Toolchain recipe differs; prepare a fresh toolchain')
    for name in ('wimlib-imagex','cabextract','x86_64-w64-mingw32-gcc','x86_64-w64-mingw32-g++'):
        if not (root / 'usr/bin' / name).is_file():
            raise ValueError('Toolchain missing '+name)
    return data


def command(root, work, argv, engine='bwrap'):
    """Expose only the private workspace and read-only checkout, never the desktop."""
    if not root.is_absolute() or not work.is_absolute() or work == Path.home() or work == Path('/'):
        raise ValueError('Use dedicated absolute toolchain and work directories')
    environment = {'PATH':'/opt/wine/usr/bin:/usr/bin:/bin','HOME':'/tmp',
                   'LC_ALL':'C.UTF-8','PENGUIN_TOOLCHAIN':'1',
                   'LD_LIBRARY_PATH':'/opt/wine/usr/lib/wine/x86_64-unix:/usr/lib/x86_64-linux-gnu',
                   'PYTHONUNBUFFERED':'1'}
    if engine == 'bwrap':
        args = ['bwrap','--unshare-all','--die-with-parent','--ro-bind',str(root),'/',
                '--bind',str(work),'/work','--ro-bind',str(ROOT),'/project',
                '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--tmpfs','/run',
                '--clearenv','--chdir','/work']
        for k,v in environment.items(): args += ['--setenv',k,v]
    elif engine == 'podman':
        # Private rootfs can be labelled container_file_t by the caller. Do not
        # disable SELinux or relabel the user's home/repository. Copy the small
        # source checkout under the dedicated workspace before this invocation.
        source = work / 'source'
        if not (source / 'tools/packaging').is_dir():
            raise ValueError('Podman requires a source copy at workspace/source')
        args = ['podman','run','--rm','--rootfs','--read-only','--network=none',
                '--userns=keep-id','--user',f'{os.getuid()}:{os.getgid()}',
                '--cap-drop=ALL','--security-opt=no-new-privileges',
                '--tmpfs=/tmp:rw,nosuid,nodev,mode=1777','--tmpfs=/run:rw,nosuid,nodev,mode=755',
                '--workdir=/work','-v',f'{work}:/work:Z','-v',f'{source}:/project:ro,Z']
        for k,v in environment.items(): args += ['--env',k+'='+v]
        args += [str(root)]
        return args + list(map(str,argv))
    else:
        raise ValueError('Unknown acquisition engine')
    return args + ['--', *map(str,argv)]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('fetch','prepare','run'))
    p.add_argument('--cache',type=Path)
    p.add_argument('--root',type=Path)
    p.add_argument('--work',type=Path)
    p.add_argument('--engine',choices=('bwrap','podman'),default='bwrap')
    a,argv=p.parse_known_args()
    if a.action=='fetch': fetch(external_path(a.cache))
    elif a.action=='prepare': prepare(external_path(a.cache),external_path(a.root))
    else:
        root,work=external_path(a.root),external_path(a.work)
        verify(root)
        if argv and argv[0]=='--':argv=argv[1:]
        if not argv:p.error('run requires a command after --')
        subprocess.run(command(root,work,argv,a.engine),check=True)

if __name__=='__main__':
    os.umask(0o077)
    main()
