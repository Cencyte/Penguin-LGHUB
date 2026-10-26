#!/usr/bin/env python3
"""Create the two requested Desktop entries using the existing G HUB lab files."""
from pathlib import Path
import shutil
import subprocess

from desktop_launch import APP, ASSETS, LAB, SCRIPT, check


def quote(value):
    text = str(value).replace('\\', '\\\\').replace('"', '\\"')
    return '"' + text.replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def main():
    desktop = Path(subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True).strip())
    if not desktop.is_dir():
        raise RuntimeError(f'Desktop directory is missing: {desktop}')
    ASSETS.mkdir(exist_ok=True)
    installer = ASSETS / 'lghub_setup.exe'
    if not installer.exists():
        candidates = (LAB / 'prefix/drive_c/users/firesongz/AppData/Local/Temp').glob('ghub-*/lghub_setup.exe')
        source = max(candidates, key=lambda p: p.stat().st_mtime)
        shutil.copy2(source, installer)
    shutil.copy2(APP / 'resources/icon.png', ASSETS / 'icon.png')
    check()
    command = '/usr/bin/python3 ' + quote(SCRIPT)
    for mode, name in [('app', 'G HUB (Patched Wine)'), ('installer', 'G HUB Installer (Patched Wine)')]:
        entry = desktop / (name + '.desktop')
        entry.write_text(f'''[Desktop Entry]
Type=Application
Version=1.0
Name={name}
Comment=Open the existing Logitech G HUB test installation with the Wine compatibility patches
Exec={command} {mode}
TryExec=/usr/bin/python3
Icon={ASSETS / 'icon.png'}
Path={LAB}
Terminal=false
StartupNotify=false
Categories=Utility;
Actions=Stop;Logs;

[Desktop Action Stop]
Name=Quit G HUB session
Exec={command} stop

[Desktop Action Logs]
Name=Open launch logs
Exec={command} logs
''')
        entry.chmod(0o755)
        subprocess.run(['desktop-file-validate', str(entry)], check=True)
        print(entry)


if __name__ == '__main__':
    main()
