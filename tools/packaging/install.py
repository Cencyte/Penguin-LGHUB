#!/usr/bin/env python3
"""One terminal entry point for the version-pinned Penguin-LGHUB installation."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode=True
os.environ["PYTHONDONTWRITEBYTECODE"]="1"

from windows_media_acquire import ROOT, digest_file, space_check
from installer_ui import Presentation, STYLE
import host_preflight
import installation_state
import install_transaction
import install_storage

HELP='''USAGE
  ./install.sh [--install] [--verbose] [--directory <path>] [--engine <bwrap|podman>]
  ./install.sh --check [--verbose]
  ./install.sh --uninstall [--directory <path>] [--verbose]
  ./install.sh --diag [--directory <path>] [--verbose]
  ./install.sh --update [--verbose]

SUMMARY
  Prepare a private, pinned G HUB installation from verified downloads and
  public compatibility source. Keep system Wine and existing prefixes intact.
  Commit after verification; roll back failed attempts and retain diagnostics.
  Run as your normal desktop user; sudo is used only for device-access rules.
  Installation is the default when no action flag is supplied.

FLAGS
  --install                Run the complete installation pipeline (default).
  --check                  Record host compatibility without installing anything.
  --uninstall              Remove a managed installation, including G HUB settings.
  --diag                   Save a sanitized .tar.gz report in this project folder.
  --update                 Update installer source only; never the installed app.
  --directory <path>       Installation storage (default: ~/.local/share/penguin-lghub).
  --engine <bwrap|podman>   Private acquisition engine (default: bwrap).
  --jobs <number>          Wine build jobs, 1–16 (default: 4).
  --verbose                Also print child-command output below step headers.
  --help                   Show this help and exit.

EXAMPLES
  ./install.sh
  ./install.sh --verbose
  ./install.sh --directory /path/with/enough/space
  ./install.sh --check
  ./install.sh --uninstall
  ./install.sh --diag
  ./install.sh --update
'''


def parser():
    p=argparse.ArgumentParser(add_help=False,allow_abbrev=False)
    action=p.add_mutually_exclusive_group();action.add_argument('--install',action='store_true');action.add_argument('--check',action='store_true');action.add_argument('--uninstall',action='store_true')
    action.add_argument('--diag',action='store_true');action.add_argument('--update',action='store_true')
    p.add_argument('--help',action='store_true');p.add_argument('--verbose',action='store_true')
    p.add_argument('--directory',type=Path,default=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'penguin-lghub')
    p.add_argument('--engine',choices=('bwrap','podman'),default='bwrap');p.add_argument('--jobs',type=int,default=4)
    return p


def fingerprint(root=ROOT):
    files=sorted(p for folder in ('tools/packaging','tools/wine','packaging','patches/wine','assets') for p in (root/folder).rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.log') and p.name!='source-publication.json')
    return hashlib.sha256(json.dumps({str(p.relative_to(root)):digest_file(p)['sha256'] for p in files},sort_keys=True).encode()).hexdigest()


def save(path,data):
    temp=path.with_suffix('.new');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(path)


def pipeline(args,ui,report):
    install_transaction.execute(args.directory, fingerprint(), STYLE['project_version'], ui,
        lambda directory,state: _pipeline(args,ui,report,directory,state))


def _pipeline(args,ui,report,directory,state):
    source_id=fingerprint()
    if state['source']!=source_id:
        raise ValueError('This installation belongs to another source revision; choose a new --directory to preserve it.')
    capacity=install_storage.capacity_plan(ROOT)
    ui.step('System','Check installation working space')
    ui.output(f"Offline capacity budget: {capacity['required_free_bytes']/1024**3:.0f} GiB free, "
              'including filesystem allowance and 2 GiB headroom\n')
    space_check(directory,capacity['required_free_bytes']-install_storage.FREE_SPACE_FLOOR)
    (directory/'provenance').mkdir(exist_ok=True)
    save(directory/'provenance/storage-budget.json',capacity)
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
    def step(name,category,description,command,check=None):
        ui.step(category,description)
        state['active']=name;state['attempts'][name]=1;installation_state.save(directory,state)
        ui.run(command)
        if check:ui.run(check)
        saved=installation_state.load(directory)
        for key in ('launchers','created_directories'):state[key]=saved[key]
        state['completed'].append(name);state.pop('active',None);installation_state.save(directory,state)
        if name in install_storage.PRUNE_AFTER:
            install_storage.prune_verified(directory,name,ui)
    step('tool-fetch','Downloads','Verify private toolchain inputs',cmd('toolchain.py','fetch','--cache',cache))
    sdk=directory/'toolchain'
    step('tool-prepare','Tools','Assemble private acquisition and build tools',cmd('toolchain.py','prepare','--cache',cache,'--root',sdk))
    wine=work/'built-wine'
    # Build remains in the reproducible namespace; no target compiler or Wine package needed.
    step('wine','Wine','Build the custom Wine runtime from pinned source',cmd('build_release_runtime.py','--work',work,'--toolchain',sdk,'--jobs',args.jobs),cmd('wine_runtime.py','verify','--runtime',wine))
    step('linux-fetch','Downloads','Verify the pinned Linux runtime',cmd('linux_runtime.py','fetch','--cache',cache))
    linux=directory/'linux-runtime'
    step('linux','Tools','Prepare Linux libraries',cmd('linux_runtime.py','prepare','--cache',cache,'--output',linux),cmd('linux_runtime.py','verify','--linux-runtime',linux))
    step('test-fetch','Downloads','Verify bundled launcher helpers',cmd('linux_probe.py','fetch','--cache',cache))
    helpers=directory/'helpers'
    step('helpers','Tools','Prepare bundled launcher helpers',cmd('linux_probe.py','prepare','--cache',cache,'--output',helpers))
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
         cmd('windows_media_acquire.py','verify','--directory',work/'windows-reconstructed/dlls'))
    depots=work/'logitech-cache';stage=work/'logitech-stage'
    step('depots','Downloads','Verify the pinned Logitech depots',cmd('logitech_depots.py','fetch','--work-dir',depots))
    step('stage','G HUB','Extract the verified application',cmd('logitech_depots.py','stage','--work-dir',depots,'--output-dir',stage),
         cmd('logitech_depots.py','verify','--output-dir',stage))
    candidate=work/'candidate'
    step('prefix','G HUB','Generate a fresh prefix and apply compatibility fixes',cmd('prepare_clean_prefix.py',
        '--stage',stage,'--cache',depots,'--output',candidate,'--runtime',wine,
        '--linux-runtime',linux,'--windows-dlls',work/'windows-reconstructed/dlls'),
        cmd('prepare_clean_prefix.py','--output',candidate,'--verify-only'))
    desktop=directory/'desktop'
    step('loader','Verify','Check the private runtime without desktop or device access',
        cmd('linux_runtime.py','audit','--linux-runtime',linux,'--wine',wine,'--output',work/'loader-audit'))
    step('ui','Verify','Test first-run UI and tray registration in an isolated display',
        cmd('linux_ui_probe.py','probe','--candidate',candidate,'--linux-runtime',linux,'--wine',wine,
            '--test-tools',helpers,'--output',work/'ui-audit'))
    # Dispose of the isolated UI copy before creating the desktop copy: at
    # most two complete prefixes coexist, even with the full device catalog.
    step('desktop','Desktop','Prepare the desktop installation',cmd('desktop_runtime.py','prepare',
        '--candidate',candidate,'--linux-runtime',linux,'--wine',wine,'--test-tools',helpers,'--output',desktop))
    step('profiles','Verify','Verify X11 and Wayland automatic-profile components',
         cmd('profile_support.py','--config',desktop/'desktop.json'))
    step('graphics','Verify','Inspect imported graphics and audio without opening an application',
        cmd('session_probe.py','--config',desktop/'desktop.json','--output',work/'session-audit/report.json'))
    # Only this small, reviewable helper runs under sudo. No application runs as root.
    step('rules','System','Install scoped Logitech device permissions',
         ['sudo',sys.executable,str(code/'tools/packaging/install_device_rules.py'),
          '--installation-id',state['installation_id']])
    destination=subprocess.check_output(['xdg-user-dir','DESKTOP'],text=True).strip()
    if not Path(destination).is_absolute():raise ValueError('Cannot resolve the Desktop directory')
    name='Penguin-LGHUB.desktop'
    step('launcher','Desktop','Install the launcher and project icon',cmd('desktop_runtime.py','install-entry',
        '--config',desktop/'desktop.json','--destination',Path(destination)/name,'--session','auto'))


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    args=parser().parse_args(argv)
    if args.help:print(HELP);return 0
    args.install=not(args.check or args.uninstall or args.diag or args.update)
    if os.geteuid()==0:raise ValueError('Run as your normal desktop user; setup requests sudo when needed.')
    if args.uninstall:
        from uninstall import run
        return run(args.directory,args.verbose)
    if args.diag:
        from diagnostics import run
        return run(args.directory,args.verbose)
    if args.update:
        from installer_update import run
        return run(args.verbose)
    if not 1<=args.jobs<=16:raise ValueError('--jobs must be 1–16')
    state=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'penguin-lghub'
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
    return present_install(args,state,stamp)


def present_install(args,state,stamp):
    ui=Presentation(state/('install-'+stamp+'.log'),args.verbose)
    ui.check_results=args.check
    def inspect():
        report=host_preflight.inventory(progress=ui.begin_check,result=ui.finish_check);save(state/('host-'+stamp+'.json'),report)
        import device_compatibility
        coverage=device_compatibility.inspect()
        device_compatibility.present(coverage,ui)
        report['device_compatibility']=coverage
        report.setdefault('checks',[]).append({'category':'Verify','description':'Checking device compatibility','status':coverage['status']})
        report['errors'].extend(coverage['errors'])
        report['warnings'].extend(coverage['warnings'])
        description='Check installation filesystem links and private permissions'
        ui.begin_check('System',description)
        try:
            report['installation_filesystem']=install_storage.check_filesystem(args.directory)
            status='OK'
        except ValueError as error:
            report['errors'].append(str(error))
            status='FAIL'
        ui.finish_check('System',description,status)
        report.setdefault('checks',[]).append({'category':'System','description':description,'status':status})
        save(state/('host-'+stamp+'.json'),report)
        ui.output(json.dumps(report,indent=2)+'\n')
        for warning in report['warnings']:ui.write('Note: '+warning)
        if report['errors']:raise RuntimeError('\n'.join(report['errors']))
        if args.engine=='podman' and not shutil.which('podman'):
            raise RuntimeError('Podman is not installed; use the default bubblewrap engine on this host.')
        return report
    try:
        ui.header()
        with ui.activity():
            if args.check:
                inspect();ui.write('Host checks completed. Actual application/device acceptance remains pending.');return 0
            install_transaction.execute(args.directory,fingerprint(),STYLE['project_version'],ui,
                lambda directory,receipt: _pipeline(args,ui,inspect(),directory,receipt))
        ui.write('Installation committed. Open the Penguin-LGHUB launcher when ready.')
        ui.complete('Installation')
        return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        import traceback
        traceback.print_exc(file=ui.log);ui.log.flush()
        ui.fail_check()
        ui.write('Setup stopped: '+str(error));ui.write('Details: '+str(ui.log_path));return 1
    except KeyboardInterrupt:
        ui.write('Setup interrupted. See the rollback result above. Diagnostics: '+str(ui.log_path));return 130
    finally:ui.close()

if __name__=='__main__':
    os.umask(0o077)
    raise SystemExit(main())
