"""Verified optional Wine desktop initialization guard; never replace the base runtime."""
import json
import os
from pathlib import Path
import shutil

from windows_media_acquire import ROOT, digest_file

LOCK = ROOT / 'packaging/wine-desktop-init.lock.json'


def recipe():
    lock = json.loads(LOCK.read_text())
    if digest_file(ROOT / lock['patch'])['sha256'] != lock['patch_sha256']:
        raise ValueError('Desktop initialization patch differs from its recipe')
    return lock


def overlay(config, root):
    lock = recipe()
    expected = 'wine-overlays/' + lock['module_sha256'] + '.so'
    if (config['desktop_init_override'] != expected or
            config['fixed_files'].get(expected) != lock['module_sha256']):
        raise ValueError('Unrecognized Desktop initialization overlay')
    source = root / expected
    if (source.is_symlink() or not source.resolve().is_relative_to(root.resolve()) or
            digest_file(source)['sha256'] != lock['module_sha256']):
        raise ValueError('Desktop initialization overlay changed')
    target = Path(config['wine']) / lock['target']
    if digest_file(target)['sha256'] != lock['base_sha256']:
        raise ValueError('Desktop initialization overlay requires the pinned base driver')
    return source, target


def stage(config_path, module):
    # Caller's full installation verification precedes this function. Keeping
    # the new file beside desktop.json preserves relocation and rollback.
    config = json.loads(config_path.read_text())
    root = config_path.parent
    lock = recipe()
    if module.is_symlink() or digest_file(module)['sha256'] != lock['module_sha256']:
        raise ValueError('Unrecognized Desktop initialization module')
    if digest_file(Path(config['wine']) / lock['target'])['sha256'] != lock['base_sha256']:
        raise ValueError('Desktop initialization overlay requires the pinned base driver')
    folder = root / 'wine-overlays'
    folder.mkdir(exist_ok=True)
    if folder.is_symlink():
        raise ValueError('Wine overlay directory must not be a symlink')
    name = 'wine-overlays/' + lock['module_sha256'] + '.so'
    temporary = folder / f'prepared-{os.getpid()}.so'
    try:
        with temporary.open('xb') as stream, module.open('rb') as source:
            shutil.copyfileobj(source, stream)
        if digest_file(temporary)['sha256'] != lock['module_sha256']:
            raise ValueError('Desktop initialization module changed during staging')
        temporary.replace(root / name)
        config['fixed_files'][name] = lock['module_sha256']
        config['desktop_init_override'] = name
        overlay(config, root)
        backup = root / ('desktop-before-init-' + lock['module_sha256'][:12] + '.json')
        if not backup.exists():
            with backup.open('xb') as stream:
                stream.write(config_path.read_bytes())
        staged = config_path.with_suffix(f'.{os.getpid()}.new')
        with staged.open('x') as stream:
            stream.write(json.dumps(config, indent=2) + '\n')
        staged.replace(config_path)
        return {'overlay': name, 'takes_effect': 'next cold launch', 'previous_config': str(backup)}
    finally:
        temporary.unlink(missing_ok=True)
