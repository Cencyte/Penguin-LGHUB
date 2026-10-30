"""Distribution policy shared by installation and desktop diagnostics.

Package databases supply version evidence only. Installation behavior otherwise
uses measured capabilities, session type and an explicitly selected engine.
"""
from pathlib import Path

PACKAGE_FAMILY = {'manjaro': 'arch', 'cachyos': 'arch', 'bazzite': 'rpm'}
PACKAGE_QUERIES = {
    'arch': ('pacman', '-Q', 'kwin', 'plasma-workspace', 'xorg-xwayland', 'libei',
             'xdg-desktop-portal', 'xdg-desktop-portal-kde', 'pipewire',
             'bubblewrap', 'mesa', 'nvidia-utils'),
    'rpm': ('rpm', '-q', 'kwin', 'plasma-workspace', 'terra-xorg-x11-server-Xwayland',
            'xorg-x11-server-Xwayland', 'libei', 'xdg-desktop-portal',
            'xdg-desktop-portal-kde', 'pipewire', 'bubblewrap',
            'selinux-policy-targeted', 'podman'),
}


def os_identity(path=Path('/etc/os-release')):
    result = {}
    for line in path.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            result[key] = value.strip('"\'')
    return result


def package_query(identity):
    family = PACKAGE_FAMILY.get(identity.get('ID'))
    return list(PACKAGE_QUERIES[family]) if family else []
