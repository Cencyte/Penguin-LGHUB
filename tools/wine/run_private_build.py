#!/usr/bin/env python3
"""Run a command with three source-built Wine 10.20 DLLs privately mounted.

Usage: run_private_build.py --build-dir BUILD --prefix COPIED_PREFIX COMMAND ...
The command inherits the invoking display; automated tests must use Xvfb. No DLL
is installed on the host. Build setupapi, ntoskrnl.exe and kernelbase after applying
patches/wine/0001 and 0002 to Wine 10.20 with build_private_wine.sh; use a stopped,
installed G HUB lab prefix.
"""
import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--freeze-ghub', action='store_true',
                        help='mount the G HUB application directory read-only in this private namespace')
    parser.add_argument('--frontend-archive', type=Path,
                        help='read-only private replacement for G HUB resources/app.asar')
    parser.add_argument('--wait-build-dir', type=Path,
                        help='optional matched ntdll/Unix ntdll/wineserver experiment')
    parser.add_argument('--d3d11-build-dir', type=Path,
                        help='optional isolated D3D11 diagnostic build')
    parser.add_argument('--dxgi-build-dir', type=Path,
                        help='optional matching DXGI shared-texture implementation')
    parser.add_argument('--wined3d-build-dir', type=Path,
                        help='optional matching WineD3D backend for the D2D flush-counter experiment')
    parser.add_argument('--windows-ui-build-dir', type=Path,
                        help='optional isolated Windows.UI CoreWindow factory backport')
    parser.add_argument('--windows-storage-build-dir', type=Path,
                        help='optional isolated Windows.Storage memory-stream backport')
    parser.add_argument('--dwrite-build-dir', type=Path,
                        help='optional isolated DirectWrite private text-analysis implementation')
    parser.add_argument('--window-build-dir', type=Path,
                        help='optional matched Win32u PE/Unix child-composition experiment')
    parser.add_argument('--composition-build-dir', type=Path,
                        help='matched Win32u/ntdll/wineserver input-sink build; implies window and wait builds')
    parser.add_argument('--threadpool-build-dir', type=Path,
                        help='private matched threadpoolwinrt.dll with ThreadPoolTimer support')
    parser.add_argument('--shcore-build-dir', type=Path,
                        help='private Shcore feature-usage compatibility export')
    parser.add_argument('--x11-build-dir', type=Path,
                        help='private X11 driver with mouse size/move release handling')
    parser.add_argument('--dwmapi-build-dir', type=Path,
                        help='private Dwmapi contact-feedback compatibility export')
    parser.add_argument('--ninput-build-dir', type=Path,
                        help='private interaction-context implementation')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.error('supply a command')
    if args.composition_build_dir:
        composition = args.composition_build_dir.resolve(strict=True)
        if not (composition / 'ghub-composition-protocol').is_file():
            parser.error('build the input-sink components with build_private_window.sh --input-sink first')
        for value in (args.wait_build_dir, args.window_build_dir):
            if value and value.resolve(strict=True) != composition:
                parser.error('--composition-build-dir requires all three components from the same build')
        args.wait_build_dir = args.window_build_dir = composition
    if args.window_build_dir:
        window_build = args.window_build_dir.resolve(strict=True)
        # New sink requests change the private server protocol. Never mount
        # only its Win32u while leaving an older ntdll/server pair in place.
        if (window_build / 'ghub-composition-protocol').exists() and (
                not args.wait_build_dir or args.wait_build_dir.resolve(strict=True) != window_build):
            parser.error('this Win32u requires --composition-build-dir with its matched ntdll/server')
    build = args.build_dir.resolve(strict=True)
    prefix = args.prefix.resolve(strict=True)
    if prefix == Path.home() / '.wine' or not (prefix / 'drive_c/Program Files/LGHUB/lghub_agent.exe').is_file():
        parser.error('supply a separate copied prefix with G HUB installed')
    frontend = ''
    frontend_target = prefix / 'drive_c/Program Files/LGHUB/resources/app.asar'
    if args.frontend_archive:
        frontend = args.frontend_archive.resolve(strict=True)
        if (not args.freeze_ghub or not frontend.is_file() or not frontend_target.is_file()
                or not frontend_target.resolve().is_relative_to(prefix)
                or frontend == frontend_target.resolve()):
            parser.error('supply a separate frontend archive with --freeze-ghub')
    if subprocess.check_output(['wine', '--version'], text=True).strip() != 'wine-10.20':
        parser.error('this source patch was tested with Wine 10.20 only')
    files = []
    for module, name in [('setupapi', 'setupapi.dll'), ('ntoskrnl.exe', 'ntoskrnl.exe'),
                         ('kernelbase', 'kernelbase.dll')]:
        source = build / 'dlls' / module / 'x86_64-windows' / name
        target = Path('/usr/lib/wine/x86_64-windows') / name
        if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
            parser.error(f'missing or invalid private DLL: {source}')
        files.extend([str(source), str(target)])
    if args.wait_build_dir:
        wait_build = args.wait_build_dir.resolve(strict=True)
        for relative, installed in [
            ('dlls/ntdll/x86_64-windows/ntdll.dll', '/usr/lib/wine/x86_64-windows/ntdll.dll'),
            ('dlls/ntdll/ntdll.so', '/usr/lib/wine/x86_64-unix/ntdll.so'),
            ('server/wineserver', '/usr/bin/wineserver'),
        ]:
            source, target = wait_build / relative, Path(installed)
            if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
                parser.error(f'missing or invalid matched wait-completion component: {source}')
            files.extend([str(source), str(target)])
    if args.d3d11_build_dir:
        source = args.d3d11_build_dir.resolve(strict=True) / 'dlls/d3d11/x86_64-windows/d3d11.dll'
        target = Path('/usr/lib/wine/x86_64-windows/d3d11.dll')
        if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
            parser.error(f'missing or invalid private D3D11 DLL: {source}')
        files.extend([str(source), str(target)])
    if args.wined3d_build_dir:
        if not args.d3d11_build_dir:
            parser.error('--wined3d-build-dir requires --d3d11-build-dir')
        source = args.wined3d_build_dir.resolve(strict=True) / 'dlls/wined3d/x86_64-windows/wined3d.dll'
        target = Path('/usr/lib/wine/x86_64-windows/wined3d.dll')
        if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
            parser.error(f'missing or invalid private WineD3D DLL: {source}')
        files.extend([str(source), str(target)])
    if args.dxgi_build_dir:
        if not args.d3d11_build_dir or not args.wined3d_build_dir:
            parser.error('--dxgi-build-dir requires matching D3D11 and WineD3D builds')
        if any(p.resolve(strict=True) != args.dxgi_build_dir.resolve(strict=True)
               for p in (args.d3d11_build_dir, args.wined3d_build_dir)):
            parser.error('DXGI, D3D11 and WineD3D must come from the same shared-texture build')
        source = args.dxgi_build_dir.resolve(strict=True) / 'dlls/dxgi/x86_64-windows/dxgi.dll'
        target = Path('/usr/lib/wine/x86_64-windows/dxgi.dll')
        if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
            parser.error(f'missing or invalid private DXGI DLL: {source}')
        files.extend([str(source), str(target)])
    if args.dwrite_build_dir:
        source = args.dwrite_build_dir.resolve(strict=True) / 'dlls/dwrite/x86_64-windows/dwrite.dll'
        target = Path('/usr/lib/wine/x86_64-windows/dwrite.dll')
        if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
            parser.error(f'missing or invalid private DirectWrite DLL: {source}')
        files.extend([str(source), str(target)])
    if args.threadpool_build_dir:
        source = args.threadpool_build_dir.resolve(strict=True) / 'dlls/threadpoolwinrt/x86_64-windows/threadpoolwinrt.dll'
        target = Path('/usr/lib/wine/x86_64-windows/threadpoolwinrt.dll')
        marker = args.threadpool_build_dir.resolve(strict=True) / 'ghub-threadpool-timer-protocol'
        if not marker.is_file():
            parser.error('build the private ThreadPoolTimer module with build_private_threadpool.sh first')
        if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
            parser.error(f'missing or invalid private ThreadPoolTimer DLL: {source}')
        files.extend([str(source), str(target)])
        target = prefix / 'drive_c/windows/system32/threadpoolwinrt.dll'
        if not target.is_file() or not target.resolve().is_relative_to(prefix):
            parser.error(f'missing or external copied-prefix ThreadPoolTimer DLL: {target}')
        files.extend([str(source), str(target)])
    if args.windows_ui_build_dir:
        source = args.windows_ui_build_dir.resolve(strict=True) / 'dlls/windows.ui/x86_64-windows/windows.ui.dll'
        target = Path('/usr/lib/wine/x86_64-windows/windows.ui.dll')
        if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
            parser.error(f'missing or invalid private Windows.UI DLL: {source}')
        files.extend([str(source), str(target)])
        # This Wine 10.20 prefix DLL exports the module name "windows.ui"
        # without ".dll". The loader searches WINEDLLPATH for that exact name
        # and falls back to the prefix copy. Cover that path in the same
        # temporary mount namespace; do not replace the on-disk prefix DLL.
        target = prefix / 'drive_c/windows/system32/windows.ui.dll'
        if not target.is_file() or not target.resolve().is_relative_to(prefix):
            parser.error(f'missing or external copied-prefix Windows.UI DLL: {target}')
        files.extend([str(source), str(target)])
    for component, build_dir in (('dwmapi', args.dwmapi_build_dir), ('ninput', args.ninput_build_dir)):
        if not build_dir:
            continue
        source = build_dir.resolve(strict=True) / f'dlls/{component}/x86_64-windows/{component}.dll'
        target = Path('/usr/lib/wine/x86_64-windows') / f'{component}.dll'
        if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
            parser.error(f'missing or invalid private {component} DLL: {source}')
        files.extend([str(source), str(target)])
    if args.shcore_build_dir:
        source = args.shcore_build_dir.resolve(strict=True) / 'dlls/shcore/x86_64-windows/shcore.dll'
        targets = [Path('/usr/lib/wine/x86_64-windows/shcore.dll'),
                   prefix / 'drive_c/windows/system32/shcore.dll']
        if not targets[1].resolve().is_relative_to(prefix):
            parser.error('the copied-prefix Shcore DLL must be inside the prefix')
        for target in targets:
            if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
                parser.error(f'missing or invalid private Shcore component: {source} -> {target}')
            files.extend([str(source), str(target)])
    if args.x11_build_dir:
        x11_build = args.x11_build_dir.resolve(strict=True)
        for relative, installed in [
            ('dlls/winex11.drv/x86_64-windows/winex11.drv', '/usr/lib/wine/x86_64-windows/winex11.drv'),
            ('dlls/winex11.drv/winex11.so', '/usr/lib/wine/x86_64-unix/winex11.so'),
        ]:
            source, target = x11_build / relative, Path(installed)
            if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
                parser.error(f'missing or invalid private X11 driver: {source}')
            files.extend([str(source), str(target)])
    if args.windows_storage_build_dir:
        source = args.windows_storage_build_dir.resolve(strict=True) / 'dlls/windows.storage/x86_64-windows/windows.storage.dll'
        targets = [Path('/usr/lib/wine/x86_64-windows/windows.storage.dll'),
                   prefix / 'drive_c/windows/system32/windows.storage.dll']
        if not targets[1].resolve().is_relative_to(prefix):
            parser.error('the copied-prefix Windows.Storage DLL must be inside the prefix')
        for target in targets:
            if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
                parser.error(f'missing or invalid private Windows.Storage component: {source} -> {target}')
            files.extend([str(source), str(target)])
    if args.window_build_dir:
        window_build = args.window_build_dir.resolve(strict=True)
        for relative, installed in [
            ('dlls/win32u/x86_64-windows/win32u.dll', '/usr/lib/wine/x86_64-windows/win32u.dll'),
            ('dlls/win32u/win32u.so', '/usr/lib/wine/x86_64-unix/win32u.so'),
        ]:
            source, target = window_build / relative, Path(installed)
            if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
                parser.error(f'missing or invalid private window component: {source}')
            files.extend([str(source), str(target)])
        if (window_build / 'ghub-delegation-protocol').exists():
            source = window_build / 'dlls/user32/x86_64-windows/user32.dll'
            target = Path('/usr/lib/wine/x86_64-windows/user32.dll')
            if not source.is_file() or not target.is_file() or source.resolve() == target.resolve():
                parser.error(f'missing or invalid matched delegation User32: {source}')
            files.extend([str(source), str(target)])
    marker = ('WINEPREFIX=' + str(prefix)).encode()
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            command = (process / 'cmdline').read_bytes().split(b'\0')[0].lower()
            environment = (process / 'environ').read_bytes().split(b'\0')
        except OSError:
            continue
        if marker in environment and (b'wine' in command or command.endswith(b'.exe')):
            parser.error(f'prefix is in use by PID {process.name}; stop its Wine processes first')
    # Literal shell source; all paths and commands travel as separate argv values.
    script = '''
set -eu
count=$1
shift
while [ "$count" -gt 0 ]; do
    mount --bind "$1" "$2"
    shift 2
    count=$((count - 1))
done
frozen_app=$1
shift
if [ -n "$frozen_app" ]; then
    mount --bind "$frozen_app" "$frozen_app"
    mount -o remount,bind,ro "$frozen_app"
fi
frontend_archive=$1
frontend_target=$2
shift 2
if [ -n "$frontend_archive" ]; then
    mount --bind "$frontend_archive" "$frontend_target"
    mount -o remount,bind,ro "$frontend_target"
fi
uid=$1; gid=$2
shift 2
exec setpriv --reuid "$uid" --regid "$gid" --init-groups env "$@"
'''
    # Resolve desktop session values at launch time, including after a reboot.
    names = ['DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR',
             'DBUS_SESSION_BUS_ADDRESS', 'PULSE_SERVER', 'LANG', 'LC_ALL', 'PATH',
             'USER', 'LOGNAME', 'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP',
             'PENGUIN_GHUB_HOST_PROFILE_DIR', 'PENGUIN_GHUB_FOCUS_TOKEN', 'PENGUIN_GHUB_FOCUS_FILE']
    environment = [f'{name}={os.environ[name]}' for name in names if name in os.environ]
    environment += [f'HOME={Path.home()}', f'WINEPREFIX={prefix}',
                    'WINEDLLOVERRIDES=winemenubuilder.exe=d;mfsensorgroup=n',
                    f'WINEDEBUG={os.environ.get("WINEDEBUG", "-all")}']
    command = ['sudo', '-n', 'unshare', '--mount', '--propagation', 'private', 'bash', '-c', script,
               '_', str(len(files) // 2), *files,
               str(prefix / 'drive_c/Program Files/LGHUB') if args.freeze_ghub else '',
               str(frontend), str(frontend_target),
               str(os.getuid()), str(os.getgid()),
               *environment, *args.command]
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
