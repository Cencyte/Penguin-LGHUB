"""Read-only checks for native application-profile prerequisites and payloads."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys

from windows_media_acquire import ROOT
from wayland_session import command_result

MODULES = ('native_profiles.py', 'profile_kwin.py', 'profile_dbus.py')


def inspect(env):
    session = env.get('XDG_SESSION_TYPE')
    report = {'backend': {'x11': 'X11 foreground', 'wayland': 'KWin Wayland'}.get(session),
              'errors': [], 'libraries': {}, 'read_only': True, 'profile_switch_tested': False}
    if session not in ('x11', 'wayland'):
        report['errors'].append('Automatic profiles require a local X11 or Plasma Wayland session.')
        return report
    for library in ('libX11.so.6', 'libdbus-1.so.3'):
        try:
            ctypes.CDLL(library)
            report['libraries'][library] = True
        except OSError:
            report['libraries'][library] = False
            report['errors'].append('Missing host profile-observer library: ' + library)
    if session == 'wayland':
        status, support = command_result(['gdbus', 'call', '--session', '--dest', 'org.kde.KWin',
            '--object-path', '/KWin', '--method', 'org.kde.KWin.supportInformation'], env)
        report['wayland_compositor'] = status == 0 and 'Operation Mode: Wayland' in support
        status, interface = command_result(['gdbus', 'introspect', '--session', '--dest', 'org.kde.KWin',
            '--object-path', '/Scripting'], env)
        report['scripting_available'] = status == 0 and all(
            name in interface for name in ('org.kde.kwin.Scripting', 'loadScript', 'unloadScript'))
        if not report['wayland_compositor'] or not report['scripting_available']:
            report['errors'].append('Automatic profiles need this session’s Wayland KWin and its scripting interface.')
    return report


def verify_payload(config_path, source=ROOT):
    sys.path.insert(0, str(source / 'tools/wine'))
    import native_profiles
    config = json.loads(config_path.read_text())
    app = config_path.parent / 'prefix/drive_c/Program Files/LGHUB'
    if config.get('native_profiles') != 1 or not native_profiles.available(app):
        raise ValueError('Automatic profile adapter is missing or disabled; rebuild the current source runtime.')
    import hashlib
    if hashlib.sha256((app / 'lghub_agent.exe').read_bytes()).hexdigest() != native_profiles.AGENT_SHA256:
        raise ValueError('Automatic profile adapter requires the pinned G HUB agent.')
    for name in MODULES:
        if not (source / 'tools/wine' / name).is_file():
            raise ValueError('Missing packaged profile observer: ' + name)
    return {'native_profiles': True, 'backends': ['x11', 'kde-wayland'],
            'observer_modules': list(MODULES), 'persistent_kwin_configuration': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_payload(args.config), indent=2))
