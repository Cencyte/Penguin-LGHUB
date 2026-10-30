#!/usr/bin/env python3
"""Install exactly the project's Logitech uaccess rule; no package changes."""
import os
from pathlib import Path
import shutil
import subprocess

RULE=b'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", TAG+="uaccess"\nSUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="046d", TAG+="uaccess"\n'


def install():
    if os.geteuid()!=0:raise RuntimeError('This scoped helper requires sudo')
    directory=Path('/etc/udev/rules.d');directory.mkdir(parents=True,exist_ok=True)
    target=directory/'70-penguin-lghub.rules'
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

if __name__=='__main__':install()
