#!/usr/bin/env python3
"""Read-only API smoke test in a disposable clone of a newly generated prefix.

The clone has private network/PID namespaces, an owned Xvfb, no physical input
devices and no writable host files outside its output directory. This does not
qualify hardware, graphics, or installation on another distribution.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import socket
import subprocess
import sys
import time

from prepare_clean_prefix import ROOT, APP, environment, run_logged, verify_prepared, write_json
from windows_media_acquire import digest_file, external_path, space_check
from wine_runtime import verify_runtime


def runtime_images(runtime):
    """Observe actual mapped Wine images while this namespace's agent is alive."""
    images = set()
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            lines = (process / 'maps').read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            fields = line.split(maxsplit=5)
            if len(fields) < 6 or not fields[5].startswith('/'):
                continue
            path = Path(fields[5])
            if str(path).startswith(('/usr/lib/wine/', '/usr/lib64/wine/')) or str(path) in ('/usr/bin/wine', '/usr/bin/wineserver'):
                raise RuntimeError('A host Wine image was mapped during the packaged test')
            if path.is_relative_to(runtime):
                images.add(str(path.relative_to(runtime)))
    required = {'usr/bin/wineserver', 'usr/lib/wine/x86_64-unix/ntdll.so'}
    if not required.issubset(images):
        raise RuntimeError('Missing evidence of the packaged server/ntdll pair')
    return sorted(images)


def worker(output, runtime=None):
    if socket.if_nameindex() != [(1, 'lo')] or list(Path('/dev').glob('hidraw*')) or Path('/dev/input').exists():
        raise RuntimeError('Agent probe requires private network and no physical input devices')
    prefix = output / 'prefix'
    if os.environ.get('WINEPREFIX') != str(prefix):
        raise RuntimeError('Unexpected prefix')
    # This is a backend probe. GLX needlessly loads the host NVIDIA EGL driver
    # and can crash Xvfb when /dev has intentionally hidden all GPUs.
    Path('/tmp/.X11-unix').mkdir(mode=0o700, exist_ok=True)
    env = dict(os.environ, LIBGL_ALWAYS_SOFTWARE='1', LP_NUM_THREADS='2')
    with (output / 'xvfb.log').open('x') as log:
        x = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1280x900x24',
                              '-nolisten', 'tcp', '-extension', 'GLX'], env=env,
                             stdout=subprocess.PIPE, stderr=log)
    result = {'isolated_network': True, 'physical_devices_exposed': False, 'live_desktop_used': False,
              'agent_ready': False, 'routes_succeeded': [], 'scope': 'Backend API with no physical devices'}
    agent = None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(x.stdout, selectors.EVENT_READ)
            if not selector.select(10):
                raise RuntimeError('Owned Xvfb did not start')
        display = x.stdout.readline().decode().strip()
        if not display.isdecimal():
            raise RuntimeError('Owned Xvfb failed; see xvfb.log')
        env['DISPLAY'] = ':' + display
        with (output / 'agent.log').open('x') as log:
            agent = subprocess.Popen(['wine', str(prefix / APP / 'lghub_agent.exe')],
                                     cwd=prefix / APP, env=env, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                probe = subprocess.run(['node', str(ROOT / 'tools/wine/agent_snapshot.cjs')],
                                       env=env, capture_output=True, text=True, timeout=15)
                (output / 'last-snapshot.json').write_text(probe.stdout)
                (output / 'last-snapshot.stderr').write_text(probe.stderr)
                if probe.returncode == 0:
                    snapshot = json.loads(probe.stdout)
                    if runtime:
                        result['mapped_runtime_images'] = runtime_images(runtime)
                        result['host_wine_hidden'] = True
                    result['agent_ready'] = True
                    result['routes_succeeded'] = sorted(snapshot['responses'])
                    break
                if agent.poll() is not None:
                    result['agent_exit'] = agent.returncode
                    break
                time.sleep(1)
    finally:
        subprocess.run(['wineserver', '-k'], timeout=15, check=False)
        subprocess.run(['wineserver', '-w'], timeout=15, check=False)
        if agent:
            agent.wait(timeout=10)
        x.terminate()
        x.wait(timeout=10)
        write_json(output / 'agent-verification.json', result)
    print(json.dumps(result, indent=2))
    return 0 if result['agent_ready'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, help='override location of the same verified runtime after relocation')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.output.resolve(strict=True), args.runtime.resolve(strict=True) if args.runtime else None)
    if not args.candidate:
        parser.error('--candidate is required')
    candidate, output = external_path(args.candidate), external_path(args.output)
    if output.exists() or output.is_relative_to(candidate) or candidate.is_relative_to(output):
        raise ValueError('Use a new output separate from the prepared candidate')
    verify_prepared(candidate)
    config = json.loads((candidate / 'clean-prefix.json').read_text())
    runtime = None
    if config.get('packaged_runtime'):
        runtime = (args.runtime or Path(config['packaged_runtime']['path'])).resolve(strict=True)
        verify_runtime(runtime)
        if digest_file(runtime / 'runtime.json')['sha256'] != config['packaged_runtime']['manifest_sha256']:
            raise ValueError('Candidate and runtime identities differ')
    elif args.runtime:
        raise ValueError('This candidate was not prepared with a packaged runtime')
    space_check(output.parent, 4 * 1024**3)
    output.mkdir(mode=0o700)
    subprocess.run(['cp', '-a', '--reflink=auto', str(candidate / 'prefix'), str(output / 'prefix')], check=True)
    private = None
    if not runtime:
        private = [sys.executable, str(ROOT / 'tools/wine/run_private_build.py'), '--prefix', str(output / 'prefix'), '--freeze-ghub']
        for option, directory in config['runtime_builds'].items():
            private += [option, str(candidate / directory)]
    options = ['--runtime', str(runtime)] if runtime else []
    run_logged(output, 'probe', ['env', *(f'{k}={v}' for k,v in config['runtime_environment'].items()),
                               sys.executable, str(Path(__file__).resolve()), '--worker', '--output', str(output), *options],
               environment(output / 'prefix'), private, runtime=runtime, freeze_app=bool(runtime))
    summary = json.loads((output / 'agent-verification.json').read_text())
    if 'mapped_runtime_images' in summary:
        summary['mapped_runtime_image_count'] = len(summary.pop('mapped_runtime_images'))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(main())
