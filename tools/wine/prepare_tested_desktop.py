#!/usr/bin/env python3
"""Freeze the components of a successful native replay for user-operated testing.

Creates a new prefix and build snapshot. Does not launch applications or install
host Wine components. --install-entry creates the Desktop entry after validation.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from tested_desktop_launch import check, digest, prefix_processes


def desktop_environment(environment):
    """Drop lab-only USB/HID exclusions when promoting a replay to the Desktop.

    Native UI replays intentionally disable both drivers to avoid sending any
    commands to physical devices. That isolation must not become the user's
    device policy. Preserve every unrelated override, including update blocking.
    """
    result = dict(environment)
    if 'WINEDLLOVERRIDES' not in result:
        return result
    overrides = []
    changed = False
    for entry in result.get('WINEDLLOVERRIDES', '').split(';'):
        modules, separator, mode = entry.partition('=')
        if separator and mode.strip().lower() in ('d', ''):
            names = [name for name in modules.split(',')
                     if name.strip().lower().removesuffix('.sys') not in ('winebus', 'wineusb')]
            if not names:
                changed = True
                continue
            if len(names) != len(modules.split(',')):
                changed = True
                entry = ','.join(names) + '=' + mode
        if entry:
            overrides.append(entry)
    if changed:
        result['WINEDLLOVERRIDES'] = ';'.join(overrides)
    return result


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def install_entry(root):
    config = json.loads((root / 'launch.json').read_text())
    check(config, root)
    if desktop_environment(config['environment']) != config['environment']:
        raise RuntimeError('Lab USB/HID driver exclusions cannot be installed as a Desktop launcher')
    desktop = Path(subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True).strip())
    if not desktop.is_dir():
        raise RuntimeError('Desktop directory is missing')

    def quote(value):
        text = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
        return '"' + text.replace('%', '%%') + '"'

    script = root / 'runner/tested_desktop_launch.py'
    command = '/usr/bin/python3 ' + quote(script) + ' --config ' + quote(root / 'launch.json')
    entry = desktop / 'Penguin-LGHUB.desktop'
    if entry.exists():
        raise RuntimeError(f'Desktop entry already exists: {entry}')
    entry.write_text(f'''[Desktop Entry]
Type=Application
Version=1.0
Name=Penguin-LGHUB
Comment=Logitech G HUB with the private Wine runtime
Exec={command} app
TryExec=/usr/bin/python3
Icon={root / 'icon.png'}
Path={root}
Terminal=false
StartupNotify=false
Categories=Utility;
Actions=Stop;Snapshot;Performance;Logs;

[Desktop Action Stop]
Name=Quit this G HUB session
Exec={command} stop

[Desktop Action Snapshot]
Name=Save device and profile snapshot
Exec={command} snapshot

[Desktop Action Logs]
Name=Open logs
Exec={command} logs

[Desktop Action Performance]
Name=Launch with color-picker performance recording
Exec={command} performance
''')
    entry.chmod(0o755)
    subprocess.run(['desktop-file-validate', str(entry)], check=True)
    print(entry)


NATIVE_NINPUT_SHA256 = '0da856260c5bf440038bd7df9e0b002c55be5e540294635b87f44da0fecab90c'


def prepare_native_input(root, config, native_dll):
    """Pin the tested, user-supplied Windows gesture engine, not a trace wrapper."""
    if digest(native_dll) != NATIVE_NINPUT_SHA256:
        raise RuntimeError('Native ninput differs from the validated Windows build')
    if '--dwmapi-build-dir' not in config['builds']:
        raise RuntimeError('Native gestures require the DwmShowContact build')
    marker = root/config['builds']['--composition-build-dir']/'ghub-native-input-protocol'
    if not marker.is_file():
        raise RuntimeError('Build the matched native-input compatibility components first')
    if config['environment'].get('WINE_GHUB_INPUT_ROUTING') != '1':
        raise RuntimeError('Native gestures require composition input routing')
    target = root/'prefix/drive_c/Program Files/LGHUB/system_tray/ninput.dll'
    copy(native_dll, target)
    config['sha256'][str(target.relative_to(root))] = NATIVE_NINPUT_SHA256
    config['sha256'][str(marker.relative_to(root))] = digest(marker)
    config['environment']['WINE_GHUB_POINTER_MOUSE'] = '1'
    overrides = config['environment'].get('WINEDLLOVERRIDES', '').split(';')
    overrides = [item for item in overrides if item and item.split('=', 1)[0] != 'ninput']
    config['environment']['WINEDLLOVERRIDES'] = ';'.join(overrides+['ninput=n'])
    config['native_tray_gestures'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', type=Path, help='successful run_overlay_trace.py evidence directory')
    parser.add_argument('--command', type=Path, help='JSON argv used for that replay')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--install-entry', action='store_true')
    parser.add_argument('--solid-tray-background', action='store_true',
                        help='Disable the pinned tray resource\'s desktop-snapshot acrylic fallback')
    parser.add_argument('--native-tray-input', type=Path, help='Pinned user-supplied Windows ninput.dll for native mouse gestures')
    args = parser.parse_args()
    root = args.output.resolve()
    if args.install_entry:
        install_entry(root)
        return
    if not args.replay or not args.command:
        parser.error('--replay and --command are required for preparation')
    inputs = json.loads((args.replay / 'inputs.json').read_text())
    if json.loads((args.replay / 'result.json').read_text())['probe_exit']:
        raise RuntimeError('Replay did not complete successfully')
    source_prefix = Path(inputs['prefix']).resolve(strict=True)
    if prefix_processes(source_prefix):
        raise RuntimeError('Source prefix is running; copy only a stopped prefix')
    if shutil.disk_usage(root.parent).free < 16 * 2**30:
        raise RuntimeError('Insufficient space for a stopped-prefix snapshot')
    argv = json.loads(args.command.read_text())
    components = {
        '--build-dir': [('setupapi', 'setupapi.dll'), ('ntoskrnl.exe', 'ntoskrnl.exe'), ('kernelbase', 'kernelbase.dll')],
        '--composition-build-dir': [('ntdll', 'ntdll.dll'), ('win32u', 'win32u.dll'), ('user32', 'user32.dll')],
        '--threadpool-build-dir': [('threadpoolwinrt', 'threadpoolwinrt.dll')],
        '--d3d11-build-dir': [('d3d11', 'd3d11.dll')],
        '--dxgi-build-dir': [('dxgi', 'dxgi.dll')],
        '--wined3d-build-dir': [('wined3d', 'wined3d.dll')],
        '--dwrite-build-dir': [('dwrite', 'dwrite.dll')],
        '--windows-storage-build-dir': [('windows.storage', 'windows.storage.dll')],
        '--windows-ui-build-dir': [('windows.ui', 'windows.ui.dll')],
    }
    if '--shcore-build-dir' in argv:
        components['--shcore-build-dir'] = [('shcore', 'shcore.dll')]
    if '--x11-build-dir' in argv:
        components['--x11-build-dir'] = [('winex11.drv', 'winex11.drv')]
    if '--dwmapi-build-dir' in argv:
        components['--dwmapi-build-dir'] = [('dwmapi', 'dwmapi.dll')]
    if '--ninput-build-dir' in argv:
        components['--ninput-build-dir'] = [('ninput', 'ninput.dll')]
    root.mkdir(exist_ok=False)
    config = {'schema': 1, 'replay': str(args.replay.resolve()), 'builds': {}, 'sha256': {},
              'environment': {k: v for k, v in inputs['environment'].items()
                              if (k.startswith('GHUB_') or k.startswith('WINE_GHUB_') or k == 'WINEDLLOVERRIDES')
                              and k not in ('GHUB_TRAY_PRIVATE_LAB', 'GHUB_DCOMP_TRACE', 'GHUB_DISPATCHER_TRACE', 'GHUB_COREMSG_TRACE')}}
    config['environment']['WINEDEBUG'] = '-all,err+all'
    config['environment'] = desktop_environment(config['environment'])

    def pin(source, target, expected=None):
        if expected and digest(source) != expected:
            raise RuntimeError(f'Component differs from successful replay: {source}')
        copy(source, target)
        config['sha256'][str(target.relative_to(root))] = digest(target)

    for option, modules in components.items():
        build = Path(argv[argv.index(option) + 1]).resolve(strict=True)
        dest = root / 'builds' / build.name
        config['builds'][option] = str(dest.relative_to(root))
        for module, filename in modules:
            relative = Path('dlls') / module / 'x86_64-windows' / filename
            target = '/usr/lib/wine/x86_64-windows/' + filename
            pin(build / relative, dest / relative, inputs['private_components_sha256'][target])
        for marker in ['ghub-composition-protocol', 'ghub-delegation-protocol', 'ghub-threadpool-timer-protocol',
                       'ghub-native-input-protocol', 'ghub-host-keyboard-protocol']:
            if (build / marker).is_file():
                pin(build / marker, dest / marker)
        if option == '--composition-build-dir':
            for relative, target in [('dlls/ntdll/ntdll.so','/usr/lib/wine/x86_64-unix/ntdll.so'),
                                     ('dlls/win32u/win32u.so','/usr/lib/wine/x86_64-unix/win32u.so'),
                                     ('server/wineserver','/usr/bin/wineserver')]:
                pin(build / relative, dest / relative, inputs['private_components_sha256'][target])
        if option == '--x11-build-dir':
            relative = 'dlls/winex11.drv/winex11.so'
            target = '/usr/lib/wine/x86_64-unix/winex11.so'
            pin(build / relative, dest / relative, inputs['private_components_sha256'][target])
    subprocess.run(['cp', '-a', '--reflink=auto', str(source_prefix), str(root / 'prefix')], check=True)
    # Verify each locally deployed DLL, rather than assuming the prefix still
    # contains the one recorded by the replay.
    for item in inputs['files']:
        relative = Path(item['target']).relative_to(source_prefix)
        target = root / 'prefix' / relative
        if digest(target) != item['sha256']:
            raise RuntimeError(f'Prefix DLL differs from replay: {relative}')
        config['sha256'][str(target.relative_to(root))] = item['sha256']
    app = root / 'prefix/drive_c/Program Files/LGHUB'
    if args.solid_tray_background:
        from prepare_tray_background import patch_resources
        tray = app / 'system_tray'
        xbf, pri = patch_resources((tray/'MainWindow.xbf').read_bytes(), (tray/'resources.pri').read_bytes())
        for name, data in [('MainWindow.xbf', xbf), ('resources.pri', pri)]:
            target = tray/name
            target.write_bytes(data)
            config['sha256'][str(target.relative_to(root))] = digest(target)
        config['solid_tray_background'] = True
    for name in ['lghub_agent.exe','lghub.exe','resources/app.asar','api_information_proxy.dll','mfsensorgroup.dll',
                 'system_tray/lghub_system_tray.exe','system_tray/dwmcorei.dll',
                 'system_tray/Microsoft.Internal.FrameworkUdk.dll']:
        target = app / name
        config['sha256'][str(target.relative_to(root))] = digest(target)
    source = Path(__file__).resolve().parent
    for name in ['tested_desktop_launch.py', 'run_private_build.py', 'agent_snapshot.cjs', 'color_picker_monitor.cjs']:
        pin(source / name, root / 'runner' / name)
    copy(app / 'resources/icon.png', root / 'icon.png')
    copy(args.replay / 'inputs.json', root / 'replay-inputs.json')
    copy(args.command, root / 'replay-command.json')
    if args.native_tray_input:
        prepare_native_input(root, config, args.native_tray_input)
    config['freeze_updates'] = True
    (root / 'launch.json').write_text(json.dumps(config, indent=2) + '\n')
    check(config, root)
    print('Prepared', root, 'with', len(config['sha256']), 'pinned files; Desktop entry not installed yet.')


if __name__ == '__main__':
    os.umask(0o077)
    main()
