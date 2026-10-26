#!/usr/bin/env python3
"""Install the tested G HUB runtime bridges into an explicitly selected lab prefix.

Build first with build_runtime_bridges.sh. Run with the copied prefix stopped and
an isolated DISPLAY. This does not install G HUB, fix its WinUI tray, or enable
mouse/keyboard remapping. Fonts are optional; use your own Windows Fonts folder
to fix Chromium's missing Arial fallback. No Windows files are redistributed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

FONT_NAMES = {
    'arial.ttf': 'Arial', 'arialbd.ttf': 'Arial Bold',
    'ariali.ttf': 'Arial Italic', 'arialbi.ttf': 'Arial Bold Italic',
    'segoeui.ttf': 'Segoe UI', 'segoeuib.ttf': 'Segoe UI Bold',
    'times.ttf': 'Times New Roman', 'timesbd.ttf': 'Times New Roman Bold',
    'cour.ttf': 'Courier New', 'courbd.ttf': 'Courier New Bold',
}
ACTIVATION_KEY = (r'HKLM\Software\Microsoft\WindowsRuntime\ActivatableClassId'
                  r'\Windows.Foundation.Metadata.ApiInformation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--windows-fonts', type=Path)
    args = parser.parse_args()
    prefix = args.prefix.resolve(strict=True)
    build = args.build_dir.resolve(strict=True)
    app = prefix / 'drive_c/Program Files/LGHUB'
    if prefix == Path.home() / '.wine' or not (app / 'lghub_agent.exe').is_file():
        parser.error('select a copied lab prefix containing an installed G HUB')
    if not os.environ.get('DISPLAY'):
        parser.error('set an isolated DISPLAY for Wine registry commands')
    artifacts = ['api_information_proxy.dll', 'mfsensorgroup.dll', 'runtime_bridge_test.exe']
    for name in artifacts:
        if not (build / name).is_file():
            parser.error(f'missing {name}; run build_runtime_bridges.sh first')
    fonts = {}
    if args.windows_fonts:
        available = {p.name.lower(): p for p in args.windows_fonts.iterdir() if p.is_file()}
        if 'arial.ttf' not in available:
            parser.error('the supplied font folder must contain arial.ttf')
        fonts = {n: available[n] for n in FONT_NAMES if n in available}
    # Do not overwrite a mapped DLL. Only inspect processes of our uid.
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
    env = os.environ.copy()
    env.update(WINEPREFIX=str(prefix), WINEDEBUG='-all',
               WINEDLLOVERRIDES='winemenubuilder.exe=d;mfsensorgroup=n')
    backup = prefix / 'ghub-compat-backups' / time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, exist_ok=False)
    files = {}

    def replace(source, target):
        relative = target.relative_to(prefix)
        if target.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.ghub-new')
        with temporary.open('xb') as output:
            output.write(source.read_bytes())
        os.replace(temporary, target)
        files[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()

    for name in artifacts[:2]:
        replace(build / name, app / name)
    for name, source in fonts.items():
        replace(source, prefix / 'drive_c/windows/Fonts' / name)
    for name in ['system.reg', 'user.reg', 'userdef.reg']:
        if (prefix / name).exists():
            shutil.copy2(prefix / name, backup / name)
    try:
        subprocess.run(['wine', str(build / 'runtime_bridge_test.exe')], env=env, check=True)
        subprocess.run(['wine', 'reg', 'add', ACTIVATION_KEY, '/v', 'DllPath', '/d',
                        r'C:\Program Files\LGHUB\api_information_proxy.dll', '/f'], env=env, check=True)
        if fonts:
            content = ('Windows Registry Editor Version 5.00\n\n'
                       '[HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts]\n')
            content += ''.join(f'"{FONT_NAMES[n]} (TrueType)"="{n}"\n' for n in fonts)
            with tempfile.TemporaryDirectory(prefix='ghub-fonts-') as directory:
                reg = Path(directory) / 'fonts.reg'
                reg.write_text(content, encoding='utf-16')
                subprocess.run(['wine', 'reg', 'import', str(reg)], env=env, check=True)
        # Scope the override to this executable; other prefix applications retain
        # their existing load order. The app-local sensor DLL returns E_NOTIMPL.
        subprocess.run(['wine', 'reg', 'add',
                        r'HKCU\Software\Wine\AppDefaults\lghub_agent.exe\DllOverrides',
                        '/v', 'mfsensorgroup', '/d', 'native', '/f'], env=env, check=True)
        (backup / 'installed-files.json').write_text(json.dumps(files, indent=2) + '\n')
        print(f'Configured runtime bridges and {len(fonts)} fonts. Backup: {backup}')
    finally:
        subprocess.run(['wineserver', '-k'], env=env, check=False)
        subprocess.run(['wineserver', '-w'], env=env, check=False)


if __name__ == '__main__':
    main()
