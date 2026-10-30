"""Host-side checks and guidance for the opt-in KDE/Xwayland launcher.

Only reads host capabilities. Never grants input permissions or changes KWin.
"""
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

from host_policy import os_identity, package_query

HELP = '''Penguin-LGHUB

Log in to Plasma (Wayland), then open Penguin-LGHUB. The existing
X11 launcher remains available in a Plasma (X11) session. Quit one G HUB
session before starting the other.

When you first use a mapped key, KDE may ask to allow input control. Older
Plasma versions can show an unnamed X11 application requesting keyboard and
pointer access; newer versions can name lghub_agent.exe. Approve only the
request triggered by your mapped key. Until you approve, mapped keys may do nothing.
The launcher never approves that request for you.

Try opening and restoring the main window, the tray menu and each menu entry.
With Logitech peripherals connected, also try bindings, macros, G-Shift,
manual profile switching and ordinary lighting. In a VM, USB passthrough is
needed for physical devices to appear.

Automatic profile matching for native Wayland apps and screen-sampled lighting
are still separate work. Feature support on Wayland remains under validation.

Right-click this launcher for Check setup, Open logs, or Quit. You can
test at your own pace; there is no deadline and no automatic input recording.
'''


def socket_error(env):
    runtime = Path(env.get('XDG_RUNTIME_DIR', ''))
    display = env.get('WAYLAND_DISPLAY', '')
    if not runtime.is_absolute() or not display:
        return 'The Wayland session socket is missing. Launch from Plasma (Wayland).'
    try:
        runtime = runtime.resolve(strict=True)
        path = Path(display)
        path = (path if path.is_absolute() else runtime / path).resolve(strict=True)
        if (not path.is_relative_to(runtime) or runtime.stat().st_uid != os.getuid()
                or path.stat().st_uid != os.getuid() or not stat.S_ISSOCK(path.stat().st_mode)):
            return 'The Wayland socket must belong to your local desktop session.'
    except OSError:
        return 'The Wayland session socket is unavailable. Log in to Plasma (Wayland).'
    return None


def command_result(args, env):
    try:
        p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=8)
        return p.returncode, p.stdout + p.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def inspect(env):
    report = {'mode': 'experimental-kde-xwayland', 'errors': [], 'warnings': [],
              'permission_granted': 'not measured', 'end_to_end_qualified': False}
    error = socket_error(env)
    if error:
        report['errors'].append(error)
        return report
    # Connecting with the session's own Xauthority tests actual authentication;
    # never use xhost or edit its access control to make this succeed.
    if not shutil.which('xdpyinfo', path=env.get('PATH')):
        report['errors'].append('xdpyinfo is required to check the local Xwayland connection (xorg-xdpyinfo).')
    else:
        status, info = command_result(['xdpyinfo', '-queryExtensions'], env)
        report['x11_authenticated'] = status == 0
        for extension in ('XWAYLAND', 'XTEST'):
            report[extension.lower()] = status == 0 and bool(re.search(r'^\s+' + extension + r'\b', info, re.M))
        if not all(report.get(k) for k in ('x11_authenticated', 'xwayland', 'xtest')):
            report['errors'].append('Cannot authenticate to local Xwayland with XTEST support. Launch from the Wayland desktop; do not use xhost to bypass authentication.')
    if not shutil.which('gdbus', path=env.get('PATH')):
        report['errors'].append('gdbus is required to check KDE and its desktop portal (glib2).')
    else:
        status, _ = command_result(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus',
            '--object-path', '/org/freedesktop/DBus', '--method', 'org.freedesktop.DBus.GetNameOwner',
            'org.kde.KWin'], env)
        report['kwin_session'] = status == 0
        if status:
            report['errors'].append('KWin is unavailable on this desktop session bus. Select Plasma (Wayland).')
        status, info = command_result(['gdbus', 'introspect', '--session', '--dest',
            'org.freedesktop.portal.Desktop', '--object-path', '/org/freedesktop/portal/desktop'], env)
        report['remote_desktop_portal'] = status == 0 and 'org.freedesktop.portal.RemoteDesktop' in info and 'ConnectToEIS' in info
        if not report['remote_desktop_portal']:
            report['errors'].append('The Remote Desktop portal with input support is unavailable. Check xdg-desktop-portal, xdg-desktop-portal-kde and the Plasma session services.')
    query = package_query(os_identity())
    if query and shutil.which(query[0], path=env.get('PATH')):
        _, info = command_result(query, env)
        report['host_packages'] = info.strip().splitlines()
    report['warnings'].append('Input permission is checked by KDE when you use an assignment. These capability checks do not grant or prove permission.')
    return report
