"""Ownership receipts and exclusive access for the public installer."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from windows_media_acquire import external_path


def save(root, state):
    temporary = root / 'installation.new'
    if temporary.is_symlink():
        raise ValueError('Symlinked installation receipt')
    with temporary.open('w') as stream:
        stream.write(json.dumps(state, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(root / 'installation.json')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load(root):
    path = root / 'installation.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('No managed installation receipt in ' + str(root))
    state = json.loads(path.read_text())
    if not isinstance(state, dict):
        raise ValueError('Invalid installation ownership receipt')
    if (state.get('schema') != 2 or state.get('managed_by') != 'penguin-lghub'
            or state.get('uid') != os.getuid() or state.get('directory') != str(root)
            or not isinstance(state.get('installation_id'), str)
            or not re.fullmatch('[a-f0-9]{32}', state['installation_id'])
            or not isinstance(state.get('attempts'), dict)
            or not isinstance(state.get('completed'), list)
            or not isinstance(state.get('launchers'), dict)):
        raise ValueError('This directory lacks a matching ownership receipt. '
                         'Older lab installations require a separate cleanup review.')
    state.setdefault('created_directories', [])
    return state


def checked_directory(value):
    path = Path(value).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('Installation directory must not be a symlink')
    root = external_path(path)
    if Path.home().is_relative_to(root):
        raise ValueError('Installation directory must not contain your home directory')
    if root.exists() and (not root.is_dir() or root.stat().st_uid != os.getuid()):
        raise ValueError('Installation directory must be owned by you')
    return root


@contextmanager
def managed_directory(value, *, create=False, source=None, version=None):
    root = checked_directory(value)
    preexisted = root.exists()
    if not root.exists():
        if not create:
            raise FileNotFoundError('Installation directory does not exist: ' + str(root))
        if not root.parent.is_dir():
            raise ValueError('Choose an existing parent directory for installation storage')
        root.mkdir(mode=0o700)
    if not (root / 'installation.json').exists():
        if not create or any(root.iterdir()):
            raise ValueError('Use an empty dedicated directory or an existing managed installation')
    fd = os.open(root / '.install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another setup/removal operation is using this directory') from exc
        if (root / 'installation.json').exists():
            state = load(root)
        else:
            state = {'schema': 2, 'managed_by': 'penguin-lghub', 'directory': str(root),
                     'uid': os.getuid(), 'installation_id': uuid.uuid4().hex,
                     'directory_preexisted': preexisted,
                     'source': source, 'project_version': version, 'completed': [],
                     'attempts': {}, 'launchers': {}}
            try:
                save(root, state)
            except BaseException:
                # No payload or external registration exists yet. A failed
                # first journal write must not strand an unrecognized folder.
                for name in ('installation.new', 'installation.json', '.install.lock'):
                    (root / name).unlink(missing_ok=True)
                if not preexisted:
                    root.rmdir()
                raise
        yield root, state


def record_launchers(config, paths, content, applications=None):
    """Write intentions before creation, including additional session launchers.

    An existing external file is never adopted. Legacy development launchers
    without a managed receipt continue to work but are outside this uninstaller.
    """
    root = config.parent.parent
    if not (root / 'installation.json').exists():
        return
    if (root / '.uninstalling').exists():
        raise RuntimeError('This installation is being removed')
    state = load(root)
    if applications is not None:
        directory = applications.resolve()
        while not directory.exists():
            if str(directory) not in state['created_directories']:
                state['created_directories'].append(str(directory))
            directory = directory.parent
    for path in paths:
        path = path.parent.resolve() / path.name
        if not path.exists() and not path.is_symlink():
            state['launchers'][str(path)] = hashlib.sha256(content).hexdigest()
    save(root, state)
