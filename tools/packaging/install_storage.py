"""Offline capacity budget and disposal of verified installer workspaces."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
import shutil

GIB = 1024**3
FREE_SPACE_FLOOR = 2 * GIB

# Each entry is the last consumer of these paths. Never remove work wholesale:
# work/built-wine is part of the installed runtime, not a disposable build tree.
PRUNE_AFTER = {
    'wine': ('work/source-build',),
    'helpers': ('work/cache',),
    'windows': ('work/windows-cache', 'work/windows-control', 'toolchain', 'work/source'),
    'prefix': ('work/logitech-cache', 'work/logitech-stage', 'work/windows-reconstructed'),
    'loader': ('work/loader-audit',),
    'ui': ('work/ui-audit',),
    'desktop': ('work/candidate',),
}


def capacity_plan(source):
    data = json.loads((source / 'packaging/install-storage.json').read_text())
    if data['schema'] != 1 or data['free_space_floor_bytes'] != FREE_SPACE_FLOOR:
        raise ValueError('Invalid installer storage budget')
    for name, expected in data['recipe_locks'].items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Pinned inputs changed; update the offline storage budget: ' + name)
    components = data['components']
    for item in components.values():
        if not 0 <= item['observed_4k_bytes'] <= item['budget_bytes']:
            raise ValueError('Storage observation exceeds its component budget')
    phases = {p['name']: sum(components[c]['budget_bytes'] for c in p['components'])
              for p in data['phases']}
    total = max(phases.values()) + data['filesystem_allowance_bytes'] + FREE_SPACE_FLOOR
    return {**data, 'phase_bytes': phases, 'required_free_bytes': ((total + GIB - 1) // GIB) * GIB}


def contained(root, relative):
    path = root / relative
    if path == root or '..' in Path(relative).parts or Path(relative).is_absolute():
        raise ValueError('Invalid workspace path')
    for p in (path, *path.parents):
        if p == root:
            break
        if p.is_symlink():
            raise RuntimeError('Workspace path became a symlink: ' + str(p))
    return path


def prune_verified(root, after, ui):
    """Discard only transaction-owned inputs after their final verified use.

    A failed deletion is a failed installation and follows normal rollback.
    Small provenance, test reports and logs survive in the installed payload.
    """
    import installation_state
    import uninstall
    state = installation_state.load(root)
    if state.get('installed') or state.get('transaction') != 'preparing' or after not in state['completed']:
        raise ValueError('Workspace disposal requires a verified, uncommitted installation step')
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError('This Python cannot safely remove workspace symlinks')
    uninstall.check_tree(root)
    paths = [(relative, contained(root, relative)) for relative in PRUNE_AFTER[after]]
    evidence = contained(root, 'provenance')
    evidence.mkdir(exist_ok=True)
    ui.step('Tools', 'Remove verified temporary files')
    for relative, path in paths:
        if not path.exists():
            continue
        if not path.is_dir() or path.stat().st_uid != os.getuid():
            raise RuntimeError('Workspace is not an owned directory: ' + str(path))
        saved = contained(root, 'provenance/' + relative)
        saved.mkdir(parents=True, exist_ok=True)
        records = list(path.iterdir())
        if relative == 'work/source-build':
            records.append(contained(root, relative + '/build/config.log'))
        for item in records:
            if (not item.is_symlink() and item.is_file()
                    and item.suffix in ('.json', '.log', '.txt', '.png')):
                destination = contained(root, 'provenance/' + relative + '/' + item.name)
                shutil.copyfile(item, destination)
        # The pinned SDK contains read-only directories. Do not follow links
        # inside it (or a Wine prefix) into the user's real home directories.
        for directory, children, _ in os.walk(path, followlinks=False):
            for item in (Path(directory), *(Path(directory) / name for name in children)):
                if item.is_symlink():
                    continue
                info = item.stat()
                if info.st_uid != os.getuid():
                    raise RuntimeError('Unowned workspace directory: ' + str(item))
                if info.st_mode & 0o700 != 0o700:
                    item.chmod(info.st_mode | 0o700)
        shutil.rmtree(path)
        ui.output('Removed temporary workspace: ' + relative + '\n')


def check_filesystem(directory):
    """Exercise the dirfd link operations used by pressure-vessel, then clean up."""
    parent = Path(directory).expanduser().absolute()
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    try:
        with tempfile.TemporaryDirectory(prefix='.penguin-storage-check-', dir=parent) as tmp:
            fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                # NTFS3 can permit pathname symlinks yet reject symlinkat(dirfd).
                # Test the actual runtime requirement instead of guessing by OS.
                os.symlink('future-target', 'link', dir_fd=fd)
                if os.readlink('link', dir_fd=fd) != 'future-target':
                    raise OSError('Symlink target changed')
                source = Path(tmp)/'source'; source.write_bytes(b'private filesystem probe')
                source.chmod(0o600)
                if source.stat().st_mode & 0o777 != 0o600:
                    raise OSError('Private file permissions are not preserved')
                os.link('source', 'hardlink', src_dir_fd=fd, dst_dir_fd=fd)
                if (Path(tmp)/'hardlink').stat().st_ino != source.stat().st_ino:
                    raise OSError('Hardlink identity not preserved')
                os.rename('link', 'renamed', src_dir_fd=fd, dst_dir_fd=fd)
            finally:
                os.close(fd)
    except OSError as error:
        raise ValueError('Installation storage lacks required Linux link/permission behavior: '
                         f'{parent} ({error}). Choose a writable native Linux filesystem '
                         'such as ext4, Btrfs or ZFS with --directory.') from None
    return {'checked_parent': str(parent), 'linux_file_operations': 'passed',
            'temporary_files_removed': True}
