#!/usr/bin/env python3
"""Pinned, isolated G HUB backend checks inside the private Linux runtime.

Uses a disposable copy of a verified fresh prefix. No live display, physical
devices, host Wine, host D-Bus, or external network is exposed to the test.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import linux_runtime as linux
from linux_sdk import extract_root
from prepare_clean_prefix import verify_prepared
from probe_clean_agent import worker as agent_worker
from windows_media_acquire import (ROOT, check_asset, digest_file, download_asset,
                                  external_path, space_check, work_lock)
from wine_runtime import inventory, runtime_environment, verify_runtime

LOCK = ROOT / 'packaging/linux-test-tools.lock.json'


def build_activation_probe(output):
    if output.exists() or output.with_suffix('.json').exists():
        raise ValueError('Use a new activation-probe output')
    source = ROOT / 'tools/packaging/clean_prefix_probe.cpp'
    subprocess.run(['x86_64-w64-mingw32-g++', '-O2', '-Wall', '-Wextra', '-Werror', '-static',
                    str(source), '-o', str(output), '-lruntimeobject', '-lole32', '-luuid', '-ladvapi32'],
                   check=True, timeout=120)
    receipt = {'source_sha256': digest_file(source)['sha256'], 'exe_sha256': digest_file(output)['sha256']}
    output.with_suffix('.json').write_text(json.dumps(receipt, indent=2) + '\n')


def verify_activation_probe(exe):
    receipt = json.loads(exe.with_suffix('.json').read_text())
    if (receipt.get('source_sha256') != digest_file(ROOT / 'tools/packaging/clean_prefix_probe.cpp')['sha256']
            or receipt.get('exe_sha256') != digest_file(exe)['sha256']):
        raise ValueError('Activation probe or its source identity changed')
    # This receipt detects transfer corruption/source drift, not publisher
    # authenticity. Supply only a probe built locally or by a trusted builder.



def node_url(url):
    if url != json.loads(LOCK.read_text())['node']['url']:
        raise ValueError('Unexpected Node download URL')
    return url


def fetch(cache):
    lock = json.loads(LOCK.read_text())
    cache.mkdir(mode=0o700, exist_ok=True)
    with work_lock(cache):
        download_asset(cache, lock['node'], validate=node_url)
        for item in lock['packages']:
            download_asset(cache, item, validate=linux.support_url)


def prepare(cache, output):
    if (output.exists() or output.is_symlink() or not output.parent.is_dir()
            or output.is_relative_to(cache) or cache.is_relative_to(output)):
        raise ValueError('Use a new test-tools directory separate from downloads')
    lock = json.loads(LOCK.read_text())
    for item in (lock['node'], *lock['packages']):
        check_asset(cache / item['local_name'], item)
    space_check(output.parent, 512 * 1024**2)
    output.mkdir(mode=0o700)
    extract_root(cache / lock['node']['local_name'], output / 'node', 256 * 1024**2)
    for item in lock['packages']:
        linux.extract_support(cache / item['local_name'], output / 'xvfb')
    report = {'schema': 1, 'kind': 'penguin-headless-test-tools',
              'lock_sha256': digest_file(LOCK)['sha256'], 'files': inventory(output)}
    (output / 'runtime.json').write_text(json.dumps(report, indent=2) + '\n')


def verify(root):
    report = json.loads((root / 'runtime.json').read_text())
    if (report.get('schema') != 1 or report.get('kind') != 'penguin-headless-test-tools'
            or report.get('lock_sha256') != digest_file(LOCK)['sha256']
            or report['files'] != inventory(root)):
        raise ValueError('Headless test tools changed')


def matching_runtime(config, wine):
    report = verify_runtime(wine)
    expected = config['packaged_runtime']['manifest_sha256']
    if (digest_file(wine / 'runtime.json')['sha256'] != expected
            and report.get('derived_from', {}).get('sha256') != expected):
        raise ValueError('Runtime does not match the candidate or its SDK-derived successor')


def probe_environment(root, wine, test_tools, output, config):
    helper_bin = test_tools / 'node/node-v22.21.0-linux-x64/bin'
    env = runtime_environment(wine, output / 'prefix', dict(os.environ, **config))
    # runtime_environment removes inherited Wine settings; restore only the
    # project's recorded feature gates, never loader/server overrides.
    env.update({k: v for k, v in config.items() if k.startswith('WINE_GHUB_')})
    env['PATH'] = ':'.join(map(str, (wine / 'usr/bin', helper_bin, test_tools / 'xvfb/usr/bin'))) + ':/usr/bin:/bin'
    env['LD_LIBRARY_PATH'] = linux.library_path(root, wine) + ':' + str(test_tools / 'xvfb/usr/lib/x86_64-linux-gnu')
    env.pop('DISPLAY', None)
    env.pop('WAYLAND_DISPLAY', None)
    return env


def test_command(root, test_tools, command):
    # Xvfb invokes /usr/bin/xkbcomp, not PATH. Merge only the pinned test
    # executables/keymaps into this nested namespace; keep runtime libraries
    # unchanged. The overlay is read-only and does not alter the platform.
    bwrap = root / linux.DEPOT / 'pressure-vessel/libexec/steam-runtime-tools-0/srt-bwrap'
    # Preserve the outer sandbox's mount permissions, including its writable
    # disposable output and /tmp. It already made the host filesystem read-only.
    # A normal recursive bind adds nodev, including to /dev's submounts.
    # Preserve the outer namespace's device permissions. In headless probes
    # this is still the synthetic /dev, with no physical devices present.
    return [str(bwrap), '--bind', '/', '/', '--dev-bind', '/dev', '/dev', '--die-with-parent',
            '--overlay-src', '/usr/bin', '--overlay-src', str(test_tools / 'xvfb/usr/bin'),
            '--ro-overlay', '/usr/bin',
            '--overlay-src', '/usr/share/X11', '--overlay-src', str(test_tools / 'xvfb/usr/share/X11'),
            '--ro-overlay', '/usr/share/X11', '--', *map(str, command)]


def worker(root, wine, test_tools, output):
    expected = json.loads(linux.LOCK.read_text())['build_id']
    if f'BUILD_ID="{expected}"' not in Path('/etc/os-release').read_text():
        raise RuntimeError('Backend probe is not inside the pinned Linux userspace')
    config = json.loads((output / 'probe-config.json').read_text())
    env = probe_environment(root, wine, test_tools, output, config)
    try:
        with (output / 'activation.log').open('x') as log:
            subprocess.run([str(wine / 'usr/bin/wine'), str(output / 'clean_prefix_probe.exe')],
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        os.environ.clear()
        os.environ.update(env)
        status = agent_worker(output, wine)
        report = json.loads((output / 'agent-verification.json').read_text())
        report.update(linux_runtime_build=expected, activation_factories_verified=8,
                      registry_routes_verified=4, host_graphics_imported=False,
                      host_system_bus_exposed=False, end_to_end_qualified=False)
        (output / 'agent-verification.json').write_text(json.dumps(report, indent=2) + '\n')
        return status
    finally:
        subprocess.run([str(wine / 'usr/bin/wineserver'), '-k'], env=env, capture_output=True, timeout=10)


def probe(root, wine, test_tools, candidate, output, activation_probe=None):
    linux.verify(root)
    verify(test_tools)
    verify_prepared(candidate)
    config = json.loads((candidate / 'clean-prefix.json').read_text())
    matching_runtime(config, wine)
    if activation_probe:
        verify_activation_probe(activation_probe)
    if (output.exists() or output.is_symlink() or not output.parent.is_dir()
            or any(output.is_relative_to(p) or p.is_relative_to(output)
                   for p in (root, wine, test_tools, candidate, ROOT))):
        raise ValueError('Use a new separate probe directory')
    space_check(output.parent, 4 * 1024**3)
    output.mkdir(mode=0o700)
    (output / 'home').mkdir()
    subprocess.run(['cp', '-a', '--reflink=auto', str(candidate / 'prefix'), str(output / 'prefix')], check=True)
    if activation_probe:
        shutil.copy2(activation_probe, output / 'clean_prefix_probe.exe')
        shutil.copy2(activation_probe.with_suffix('.json'), output / 'clean_prefix_probe.json')
    else:
        build_activation_probe(output / 'clean_prefix_probe.exe')
    (output / 'probe-config.json').write_text(json.dumps(config['runtime_environment']) + '\n')
    args = ['/usr/bin/python3', str(Path(__file__).resolve()), 'worker',
            '--linux-runtime', root, '--wine', wine, '--test-tools', test_tools, '--output', output]
    with work_lock(root), (output / 'probe.log').open('x') as log:
        subprocess.run(linux.isolated_command(root, output, test_command(root, test_tools, args), freeze_app=True,
                                             read_only_paths=(root, wine, test_tools)),
                       env=linux.clean_environment(), stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=240)
    linux.verify(root)
    verify(test_tools)
    report = json.loads((output / 'agent-verification.json').read_text())
    report['mapped_runtime_image_count'] = len(report.pop('mapped_runtime_images', []))
    print(json.dumps(report, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('fetch', 'prepare', 'probe', 'worker', 'build-activation-probe'))
    p.add_argument('--activation-probe', type=Path, help='Locally built/trusted probe with adjacent .json receipt; avoids a target compiler')
    for name in ('cache', 'linux-runtime', 'wine', 'test-tools', 'candidate', 'output'):
        p.add_argument('--' + name, type=Path)
    a = p.parse_args()
    required = {'fetch': ('cache',), 'prepare': ('cache', 'output'),
                'probe': ('linux_runtime', 'wine', 'test_tools', 'candidate', 'output'),
                'worker': ('linux_runtime', 'wine', 'test_tools', 'output'),
                'build-activation-probe': ('output',)}[a.action]
    for name in required:
        if getattr(a, name) is None:
            p.error('Missing --' + name.replace('_', '-'))
        if a.action != 'worker':
            setattr(a, name, external_path(getattr(a, name)))
    if a.action == 'build-activation-probe':
        build_activation_probe(a.output)
    elif a.action == 'fetch':
        fetch(a.cache)
    elif a.action == 'prepare':
        prepare(a.cache, a.output)
    elif a.action == 'probe':
        probe(a.linux_runtime, a.wine, a.test_tools, a.candidate, a.output,
              external_path(a.activation_probe) if a.activation_probe else None)
    else:
        return worker(a.linux_runtime, a.wine, a.test_tools, a.output)
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(main())
