#!/usr/bin/env python3
"""Install exactly the project's Logitech uaccess rule; no package changes."""
import argparse
import os
import re
from pathlib import Path
import shutil
import subprocess

RULE=b'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", TAG+="uaccess"\nSUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="046d", TAG+="uaccess"\n'


def scoped_target(installation_id, directory):
    if not re.fullmatch('[a-f0-9]{32}', installation_id):
        raise ValueError('Invalid installation ID')
    uid = os.environ.get('SUDO_UID', '')
    if not uid.isdecimal() or int(uid) == 0:
        raise ValueError('Run this helper through sudo as the installing desktop user')
    return directory / f'70-penguin-lghub-{int(uid)}-{installation_id}.rules'


def install(installation_id=None, remove=False, directory=Path('/etc/udev/rules.d')):
    if os.geteuid()!=0:raise RuntimeError('This scoped helper requires sudo')
    target=scoped_target(installation_id,directory) if installation_id else directory/'70-penguin-lghub.rules'
    if remove:
        if not installation_id:raise ValueError('Removal requires an installation ID')
        if target.is_symlink():raise RuntimeError('Refusing to remove a symlinked device rule')
        if target.exists():
            if target.read_bytes()!=RULE:raise RuntimeError('Device rule has changed; preserve and review it before removal: '+str(target))
            target.unlink()
        subprocess.run(['udevadm','control','--reload-rules'],check=True)
        print('Installation-specific Logitech device rule removed.')
        return
    directory.mkdir(parents=True,exist_ok=True)
    if target.is_symlink():raise RuntimeError('Refusing to replace a symlinked device rule')
    def active_lines(data):
        return [s.strip() for s in data.splitlines() if s.strip() and not s.lstrip().startswith(b'#')]
    if target.exists() and active_lines(target.read_bytes())!=active_lines(RULE):
        raise RuntimeError('An existing Penguin-LGHUB rule differs; inspect it before replacing it')
    if not target.exists():
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
        with os.fdopen(fd,'wb') as out:out.write(RULE)
    if shutil.which('restorecon'):subprocess.run(['restorecon',str(target)],check=True)
    subprocess.run(['udevadm','control','--reload-rules'],check=True)
    # Avoid retriggering unrelated live devices. uaccess takes effect on reconnect
    # or the next local-seat activation; the launcher reports actual access.
    print('Logitech device rules installed. Reconnect devices if current ACLs lack access.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installation-id');parser.add_argument('--remove',action='store_true')
    args=parser.parse_args();install(args.installation_id,args.remove)
