#!/usr/bin/env python3
"""Read-only host inventory for installation and portable support logs."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def capture(args):
    try:
        p=subprocess.run(args,text=True,capture_output=True,timeout=12)
        return {'status':p.returncode,'output':(p.stdout+p.stderr).strip()}
    except (OSError,subprocess.TimeoutExpired) as e:
        return {'status':-1,'output':str(e)}


def os_identity(path=Path('/etc/os-release')):
    result={}
    for line in path.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k,v=line.split('=',1);result[k]=v.strip('"\'')
    return result


def inventory(env=None, sysroot=Path('/sys'), progress=None):
    # Optional presentation hook; standalone JSON consumers remain unchanged.
    progress = progress or (lambda category, description: None)
    env=os.environ if env is None else env
    progress('System', 'Check operating system, CPU and Python requirements')
    identity=os_identity();errors=[];warnings=[]
    distro=identity.get('ID','')
    if distro not in ('manjaro','cachyos','bazzite'):errors.append('Initial release targets Manjaro, CachyOS and Bazzite.')
    if platform.machine()!='x86_64':errors.append('The pinned runtime requires x86_64.')
    if sys.version_info<(3,12):errors.append('Python 3.12 or newer is required.')
    progress('Desktop', 'Check the active desktop session')
    session=env.get('XDG_SESSION_TYPE','')
    if session not in ('x11','wayland'):errors.append('Run setup from a local graphical desktop terminal.')
    if session=='wayland' and 'KDE' not in env.get('XDG_CURRENT_DESKTOP','').upper():errors.append('The validated Wayland route requires KDE Plasma.')
    progress('Tools', 'Locate required host commands')
    commands=('bwrap','python3','curl','git','zstd','udevadm','xdpyinfo','gdbus','xdg-user-dir','kdialog','sudo','podman')
    executables={n:shutil.which(n) for n in commands}
    for n in ('bwrap','python3','zstd','udevadm','xdg-user-dir','gdbus','kdialog','sudo'):
        if not executables[n]:errors.append('Missing required host command: '+n)
    progress('Tools', 'Record installed desktop and runtime package versions')
    packages=[]
    if shutil.which('rpm'):
        packages=capture(['rpm','-q','kwin','plasma-workspace','terra-xorg-x11-server-Xwayland',
            'xorg-x11-server-Xwayland','libei','xdg-desktop-portal','xdg-desktop-portal-kde',
            'pipewire','bubblewrap','selinux-policy-targeted','podman'])['output'].splitlines()
    elif shutil.which('pacman'):
        packages=capture(['pacman','-Q','kwin','xorg-xwayland','libei','xdg-desktop-portal',
            'xdg-desktop-portal-kde','pipewire','bubblewrap','mesa','nvidia-utils'])['output'].splitlines()
    progress('System', 'Inspect Logitech device access')
    nodes=[]
    for item in sorted((sysroot/'class/hidraw').glob('hidraw*')):
        uevent=item/'device/uevent'
        if uevent.exists() and ':0000046D:' in uevent.read_text().upper():
            node=Path('/dev')/item.name
            nodes.append({'node':str(node),'read_write':os.access(node,os.R_OK|os.W_OK)})
    if not nodes:warnings.append('No Logitech hidraw device detected; physical-device validation remains pending.')
    elif any(not n['read_write'] for n in nodes):warnings.append('Logitech device ACLs need the project udev rule and an active local seat; reconnect after setup if needed.')
    progress('Desktop', 'Record graphics drivers and audio availability')
    graphics=[]
    for node in sorted((sysroot/'class/drm').glob('card[0-9]*')):
        if '-' in node.name:continue
        driver=node/'device/driver'
        graphics.append({'node':node.name,'driver':driver.resolve().name if driver.exists() else 'unknown'})
    audio=Path(env.get('XDG_RUNTIME_DIR','/nonexistent'))/'pipewire-0'
    progress('System', 'Inspect SELinux policy and Wine synchronization availability')
    selinux=capture(['getenforce']) if shutil.which('getenforce') else {'status':0,'output':'not installed'}
    report={'schema':1,'os':identity,'kernel':platform.release(),'session':session,
      'packages':packages,'commands':executables,'graphics':graphics,'logitech_hidraw':nodes,
      'selinux':selinux['output'],'pipewire_socket_present':audio.is_socket(),
      'ntsync_present':Path('/dev/ntsync').exists(),'errors':errors,'warnings':warnings,
      'physical_input_tested':False,'end_to_end_qualified':False}
    if session=='wayland':
        progress('Desktop', 'Check Xwayland, KWin and the input-control portal')
        import wayland_session
        report['wayland']=wayland_session.inspect(env)
        errors.extend(report['wayland']['errors'])
    progress('Verify', 'Test an isolated user namespace')
    report['namespace_probe']=capture(['bwrap','--unshare-all','--ro-bind','/usr','/usr',
        '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
        '--proc','/proc','--dev','/dev','--','/usr/bin/true']) if executables['bwrap'] else None
    if report['namespace_probe'] and report['namespace_probe']['status']:
        errors.append('User namespace test failed; inspect the logged error and SELinux audit, without disabling policy.')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path);a=p.parse_args()
    result=inventory();text=json.dumps(result,indent=2)+'\n'
    if a.output:a.output.write_text(text)
    print(text,end='');return int(bool(result['errors']))

if __name__=='__main__':raise SystemExit(main())
