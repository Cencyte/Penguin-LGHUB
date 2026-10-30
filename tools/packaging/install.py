#!/usr/bin/env python3
"""One terminal entry point for the version-pinned Penguin-LGHUB installation."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from windows_media_acquire import ROOT, digest_file, external_path, space_check
from installer_ui import Presentation, STYLE
import host_preflight

HELP='''USAGE
  ./install.sh --install [--verbose] [--directory <path>] [--engine <bwrap|podman>]
  ./install.sh --check [--verbose]

SUMMARY
  Prepare a private, pinned G HUB installation from verified downloads and
  public compatibility source. Keep system Wine and existing prefixes intact.
  Run as your normal desktop user; sudo is used only for device-access rules.

FLAGS
  --install                Run the complete installation pipeline.
  --check                  Record host readiness without installing anything.
  --directory <path>       Installation storage (default: ~/.local/share/penguin-lghub).
  --engine <bwrap|podman>   Private acquisition engine (default: bwrap).
  --jobs <number>          Wine build jobs, 1–16 (default: 4).
  --verbose                Also print child-command output below step headers.
  --help                   Show this help and exit.

EXAMPLES
  ./install.sh --install
  ./install.sh --install --verbose
  ./install.sh --install --directory /path/with/enough/space
  ./install.sh --check
'''


def parser():
    p=argparse.ArgumentParser(add_help=False)
    action=p.add_mutually_exclusive_group();action.add_argument('--install',action='store_true');action.add_argument('--check',action='store_true')
    p.add_argument('--help',action='store_true');p.add_argument('--verbose',action='store_true')
    p.add_argument('--directory',type=Path,default=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'penguin-lghub')
    p.add_argument('--engine',choices=('bwrap','podman'),default='bwrap');p.add_argument('--jobs',type=int,default=4)
    return p


def preserve_partial(paths, attempt):
    for path in paths:
        if path.exists() or path.is_symlink():
            destination=path.with_name(path.name+f'.incomplete-{attempt}')
            if destination.exists() or destination.is_symlink():
                raise RuntimeError('Preserved partial output already exists: '+str(destination))
            path.rename(destination)


def fingerprint(root=ROOT):
    files=sorted(p for folder in ('tools/packaging','tools/wine','packaging','patches/wine','assets') for p in (root/folder).rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.log') and p.name!='source-publication.json')
    return hashlib.sha256(json.dumps({str(p.relative_to(root)):digest_file(p)['sha256'] for p in files},sort_keys=True).encode()).hexdigest()


def save(path,data):
    temp=path.with_suffix('.new');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(path)


def pipeline(args,ui,report):
    directory=external_path(args.directory)
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid!=os.getuid():raise ValueError('Installation storage must be owned by you.')
    guard=(directory/'.install.lock').open('a')
    try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('Another installation is using this directory.')
    source_id=fingerprint();receipt_path=directory/'installation.json'
    if receipt_path.exists():
        state=json.loads(receipt_path.read_text())
        if state['source']!=source_id:raise ValueError('This installation belongs to another source revision; choose a new --directory to preserve it.')
    else:state={'schema':1,'source':source_id,'completed':[],'attempts':{},'project_version':STYLE['project_version']}
    work=directory/'work';work.mkdir(exist_ok=True)
    cache=work/'cache';cache.mkdir(exist_ok=True)
    code=directory/'source'
    if code.exists() and fingerprint(code)!=source_id:
        raise RuntimeError('Private source copy is incomplete or changed; preserve it and choose a new --directory.')
    if not code.exists():
        code.mkdir()
        for name in ('tools','packaging','patches','assets'):
            shutil.copytree(ROOT/name,code/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for name in ('LICENSE','NOTICE.md'):shutil.copyfile(ROOT/name,code/name)
    python=sys.executable
    def cmd(script,*argv):return [python,str(code/'tools/packaging'/script),*map(str,argv)]
    def step(name,category,description,command,check=None,outputs=()):
        if name in state['completed']:
            ui.step(category,description+(' (verifying saved step)' if check else ' (already completed)'))
            if check:ui.run(check)
            return
        ui.step(category,description)
        if state['attempts'].get(name):preserve_partial(outputs,state['attempts'][name])
        state['active']=name;state['attempts'][name]=state['attempts'].get(name,0)+1;save(receipt_path,state)
        ui.run(command)
        state['completed'].append(name);state.pop('active',None);save(receipt_path,state)
    # Reserve for downloads, SDK, build tree, new prefix and rollback headroom.
    if not state['completed']:space_check(directory,60*1024**3)
    step('tool-fetch','Downloads','Verify private toolchain inputs',cmd('toolchain.py','fetch','--cache',cache))
    sdk=directory/'toolchain'
    step('tool-prepare','Tools','Assemble private acquisition and build tools',cmd('toolchain.py','prepare','--cache',cache,'--root',sdk),outputs=(sdk,))
    wine=work/'built-wine'
    # Build remains in the reproducible namespace; no target compiler or Wine package needed.
    step('wine','Wine','Build the custom Wine runtime from pinned source',cmd('build_release_runtime.py','--work',work,'--toolchain',sdk,'--jobs',args.jobs),cmd('wine_runtime.py','verify','--runtime',wine))
    wine=work/'built-wine'
    step('linux-fetch','Downloads','Verify the pinned Linux runtime',cmd('linux_runtime.py','fetch','--cache',cache))
    linux=directory/'linux-runtime'
    step('linux','Tools','Prepare Linux libraries',cmd('linux_runtime.py','prepare','--cache',cache,'--output',linux),cmd('linux_runtime.py','verify','--linux-runtime',linux),outputs=(linux,))
    step('test-fetch','Downloads','Verify bundled launcher helpers',cmd('linux_probe.py','fetch','--cache',cache))
    helpers=directory/'helpers'
    step('helpers','Tools','Prepare bundled launcher helpers',cmd('linux_probe.py','prepare','--cache',cache,'--output',helpers),outputs=(helpers,))
    media=work/'windows-cache'
    step('media','Downloads','Verify official Windows media and decoder source',cmd('windows_media_acquire.py','fetch','--work-dir',media,'--decoder'))
    import toolchain
    if args.engine=='podman':
        if not (work/'source').exists():shutil.copytree(code,work/'source')
        if report['selinux']=='Enforcing':
            ui.step('Tools','Label the private Podman toolchain')
            ui.run(['chcon','-R','-t','container_file_t',str(sdk)])
    worker=toolchain.command(sdk,work,['python3','/project/tools/packaging/windows_media_acquire.py','reconstruct',
        '--work-dir','/work/windows-cache','--output-dir','/work/windows-reconstructed','--control-dir','/work/windows-control'],args.engine)
    step('windows','Windows','Reconstruct and verify the two required DLLs',worker,
         cmd('windows_media_acquire.py','verify','--directory',work/'windows-reconstructed/dlls'),
         outputs=(work/'windows-reconstructed',work/'windows-control'))
    depots=work/'logitech-cache';stage=work/'logitech-stage'
    step('depots','Downloads','Verify the pinned Logitech depots',cmd('logitech_depots.py','fetch','--work-dir',depots))
    step('stage','G HUB','Extract the verified application',cmd('logitech_depots.py','stage','--work-dir',depots,'--output-dir',stage),
         cmd('logitech_depots.py','verify','--output-dir',stage),outputs=(stage,))
    candidate=work/'candidate'
    step('prefix','G HUB','Generate a fresh prefix and apply compatibility fixes',cmd('prepare_clean_prefix.py',
        '--stage',stage,'--cache',depots,'--output',candidate,'--runtime',wine,
        '--linux-runtime',linux,'--windows-dlls',work/'windows-reconstructed/dlls'),
        cmd('prepare_clean_prefix.py','--output',candidate,'--verify-only'),outputs=(candidate,))
    desktop=directory/'desktop'
    step('desktop','Desktop','Prepare the desktop installation',cmd('desktop_runtime.py','prepare',
        '--candidate',candidate,'--linux-runtime',linux,'--wine',wine,'--test-tools',helpers,'--output',desktop),outputs=(desktop,))
    step('loader','Verify','Check the private runtime without desktop or device access',
        cmd('linux_runtime.py','audit','--linux-runtime',linux,'--wine',wine,'--output',work/'loader-audit'),outputs=(work/'loader-audit',))
    step('ui','Verify','Test first-run UI and tray registration in an isolated display',
        cmd('linux_ui_probe.py','probe','--candidate',candidate,'--linux-runtime',linux,'--wine',wine,
            '--test-tools',helpers,'--output',work/'ui-audit'),outputs=(work/'ui-audit',))
    step('graphics','Verify','Inspect imported graphics and audio without opening an application',
        cmd('session_probe.py','--config',desktop/'desktop.json','--output',work/'session-audit/report.json'))
    # Only this small, reviewable helper runs under sudo. No application runs as root.
    step('rules','System','Install scoped Logitech device permissions',
         ['sudo',sys.executable,str(code/'tools/packaging/install_device_rules.py')])
    destination=subprocess.check_output(['xdg-user-dir','DESKTOP'],text=True).strip()
    if not Path(destination).is_absolute():raise ValueError('Cannot resolve the Desktop directory')
    name='Penguin-LGHUB Wayland.desktop' if report['session']=='wayland' else 'Penguin-LGHUB.desktop'
    extra=['--experimental-wayland'] if report['session']=='wayland' else []
    step('launcher','Desktop','Install the launcher and project icon',cmd('desktop_runtime.py','install-entry',
        '--config',desktop/'desktop.json','--destination',Path(destination)/name,*extra))
    state['installed']=True;state['owner_acceptance_pending']=True;save(receipt_path,state)
    ui.write('Installation complete. Open '+name+' when ready; KDE input permission and physical-device testing remain yours to confirm.')
    guard.close()


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if not argv:print(HELP.split('\nSUMMARY')[0]);return 0
    args=parser().parse_args(argv)
    if args.help:print(HELP);return 0
    if not(args.install or args.check):parser().error('Choose --install or --check')
    if os.geteuid()==0:raise ValueError('Run as your normal desktop user; setup requests sudo when needed.')
    if not 1<=args.jobs<=16:raise ValueError('--jobs must be 1–16')
    state=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'penguin-lghub'
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
    ui=Presentation(state/('install-'+stamp+'.log'),args.verbose)
    try:
        ui.header();ui.step('System','Inspect the current desktop, graphics, device access and policy')
        report=host_preflight.inventory();save(state/('host-'+stamp+'.json'),report)
        ui.log.write(json.dumps(report,indent=2)+'\n');ui.log.flush()
        for warning in report['warnings']:ui.write('Note: '+warning)
        if report['errors']:raise RuntimeError('\n'.join(report['errors']))
        if args.engine=='podman' and not shutil.which('podman'):
            raise RuntimeError('Podman is not installed; use the default bubblewrap engine on this host.')
        if args.check:ui.write('Host checks completed. Actual application/device acceptance remains pending.');return 0
        pipeline(args,ui,report);return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        import traceback
        traceback.print_exc(file=ui.log);ui.log.flush()
        ui.write('Setup stopped: '+str(error));ui.write('Details: '+str(ui.log_path));return 1
    except KeyboardInterrupt:
        ui.write('Setup interrupted. Completed steps and logs are preserved.');return 130
    finally:ui.close()

if __name__=='__main__':
    os.umask(0o077)
    raise SystemExit(main())
