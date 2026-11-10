"""Refresh this source checkout only; never invokes install/uninstall or Wine."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile

from installer_ui import Presentation
from windows_media_acquire import ROOT

REPOSITORY = 'https://github.com/Cencyte/Penguin-LGHUB.git'
API = 'https://api.github.com/repos/Cencyte/Penguin-LGHUB/commits/release'
MANIFEST = 'packaging/source-publication.json'
JOURNAL = '.penguin-installer-update'


def relative(name):
    if (not isinstance(name,str) or not name or '\\' in name or '\x00' in name
            or name.startswith('/') or any(p in ('','.','..','.git',JOURNAL) for p in name.split('/'))):
        raise ValueError('Unsafe source archive path')
    return name


def manifest(root):
    p=root/MANIFEST
    if p.is_symlink() or p.stat().st_size>8*1024**2:raise ValueError('Unsafe source manifest')
    data=json.loads(p.read_text())
    if data.get('repository')!='https://github.com/Cencyte/Penguin-LGHUB':raise ValueError('Unexpected repository identity')
    records={}
    for item in data['files']:
        name=relative(item['path'])
        if name in records or item['mode'] not in ('100644','100755') or not re.fullmatch('[0-9a-f]{64}',item['sha256']):
            raise ValueError('Invalid source publication record')
        records[name]=item
    for name,item in records.items():
        p=root/name
        if not p.resolve().is_relative_to(root.resolve()) or p.is_symlink() or not p.is_file():raise ValueError('Unsafe or absent source file: '+name)
        if p.stat().st_size!=item['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Local source changes detected; preserve them before --update: '+name)
    return records


class GitHubOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,url):
        from urllib.parse import urlsplit
        p=urlsplit(url)
        if p.scheme!='https' or p.netloc not in ('api.github.com','codeload.github.com','github.com'):
            raise ValueError('Unexpected source-download redirect')
        return super().redirect_request(req,fp,code,msg,headers,url)


def download(url,target,maximum):
    request=urllib.request.Request(url,headers={'User-Agent':'Penguin-LGHUB-installer','Accept':'application/vnd.github+json'})
    with urllib.request.build_opener(GitHubOnly()).open(request,timeout=45) as response, target.open('xb') as out:
        count=0
        while data:=response.read(1024**2):
            count+=len(data)
            if count>maximum:raise ValueError('Source download exceeds limit')
            out.write(data)


def unpack(archive,target):
    target.mkdir()
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist();total=sum(x.file_size for x in entries)
        if len(entries)>10000 or total>200*1024**2:raise ValueError('Source archive exceeds extraction limits')
        roots={PurePosixPath(x.filename).parts[0] for x in entries}
        if len(roots)!=1:raise ValueError('Source archive must have one root')
        seen=set()
        for item in entries:
            relative(item.filename.rstrip('/'))
            parts=PurePosixPath(item.filename).parts
            if len(parts)==1:continue
            name=relative('/'.join(parts[1:]));p=target/name
            mode=item.external_attr>>16
            if stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR):raise ValueError('Nonregular source archive member')
            if name in seen:raise ValueError('Duplicate source archive member')
            seen.add(name)
            if item.is_dir():p.mkdir(parents=True,exist_ok=True);continue
            p.parent.mkdir(parents=True,exist_ok=True)
            with z.open(item) as src,p.open('xb') as dst:shutil.copyfileobj(src,dst)
    records=manifest(target)
    actual={str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()}
    if actual!=set(records)|{MANIFEST}:raise ValueError('Source archive contains unpublished files')
    for name,item in records.items():(target/name).chmod(int(item['mode'][-3:],8))
    return records


def safe_target(root,name):
    path=root/relative(name)
    for p in (path,*path.parents):
        if p==root:break
        if p.is_symlink():raise ValueError('Symlink in source update path')
    if path.exists() and not path.is_file():raise ValueError('Non-file blocks source update: '+name)
    return path


def recover(root):
    journal=root/JOURNAL
    if not journal.exists():return
    if journal.is_symlink():raise ValueError('Unsafe source update journal')
    state=json.loads((journal/'state.json').read_text())
    if state['status']=='committed':shutil.rmtree(journal);return
    for name,existed in reversed(state['entries']):
        target=safe_target(root,name);backup=journal/'backup'/relative(name)
        if backup.is_symlink():raise ValueError('Unsafe source backup')
        if backup.exists():
            target.parent.mkdir(parents=True,exist_ok=True);os.replace(backup,target)
        elif not existed:target.unlink(missing_ok=True)
    shutil.rmtree(journal)


def replace_sources(root,staged,old,new):
    names=sorted(set(old)|set(new)|{MANIFEST})
    for name in names:
        target=safe_target(root,name)
        if name not in old and name!=MANIFEST and target.exists():raise ValueError('Untracked file would be overwritten: '+name)
    journal=root/JOURNAL;journal.mkdir(mode=0o700)
    state={'status':'preparing','entries':[[n,(root/n).exists()] for n in names]}
    def save():
        p=journal/'state.new'
        with p.open('w') as stream:
            json.dump(state,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(p,journal/'state.json')
    save()
    try:
        for name in names:
            target=root/name;backup=journal/'backup'/name
            if target.exists():
                backup.parent.mkdir(parents=True,exist_ok=True)
                # Keep the executable entry point present until its replacement
                # is ready. Hardlinking the old file makes rollback exact.
                os.link(target,backup)
            source=staged/name
            if source.exists():
                target.parent.mkdir(parents=True,exist_ok=True)
                pending=journal/'pending'/name
                pending.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,pending)
                os.replace(pending,target)
            else:
                target.unlink(missing_ok=True)
        manifest(root)
        state['status']='committed';save()
    except BaseException:
        recover(root);raise
    shutil.rmtree(journal)


def run(verbose=False,project=ROOT):
    import fcntl
    project=project.resolve()
    # A source update must never operate on the private installed source copy.
    if (project.parent/'installation.json').exists():raise ValueError('Run --update from your downloaded project folder, not installed private source')
    state=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'penguin-lghub'
    ui=Presentation(state/('installer-update-'+__import__('time').strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())+'.log'),verbose)
    # Lock the directory inode itself: no leftover source file, no deleting a
    # lock path while another invocation still owns its inode.
    guard=os.open(project,os.O_RDONLY|os.O_DIRECTORY)
    try:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ui.header()
        with ui.activity(),tempfile.TemporaryDirectory(prefix='penguin-source-') as tmp:
            tmp=Path(tmp)
            ui.step('Verify','Check the installer source and recover interrupted source updates')
            recover(project);old=manifest(project)
            is_git=(project/'.git').exists()
            if is_git:
                top=subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=project,text=True).strip()
                if Path(top).resolve()!=project:raise ValueError('Run --update at the repository root')
                changed=subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=project,text=True)
                if changed:raise ValueError('The Git checkout has local changes; preserve them before --update')
                branch=subprocess.check_output(['git','branch','--show-current'],cwd=project,text=True).strip()
                if branch!='release':raise ValueError('Automatic Git updates require the release branch; other branches remain unchanged')
            ui.step('Downloads','Download the latest published installer source')
            download(API,tmp/'revision.json',2*1024**2)
            revision=json.loads((tmp/'revision.json').read_text())['sha']
            if not re.fullmatch('[a-f0-9]{40}',revision):raise ValueError('Invalid upstream revision')
            download('https://codeload.github.com/Cencyte/Penguin-LGHUB/zip/'+revision,tmp/'source.zip',100*1024**2)
            ui.step('Verify','Verify the complete source manifest and archive boundaries')
            new=unpack(tmp/'source.zip',tmp/'source')
            ui.step('Tools','Update only the installer source folder')
            if is_git:
                ui.run(['git','-C',str(project),'fetch','--no-tags',REPOSITORY,revision])
                ui.run(['git','-C',str(project),'merge-base','--is-ancestor','HEAD',revision])
                ui.run(['git','-C',str(project),'merge','--ff-only',revision])
            else:replace_sources(project,tmp/'source',old,new)
        ui.notice('Installer source updated to '+revision[:12]+'. Installed Penguin-LGHUB and its settings were not changed.')
        ui.notice('Run ./install.sh when ready. Existing installations require explicit --uninstall before reinstallation.')
        return 0
    except (OSError,ValueError,RuntimeError,KeyError,subprocess.SubprocessError,zipfile.BadZipFile) as error:
        ui.notice('Installer update stopped: '+str(error),error=True);return 1
    finally:os.close(guard);ui.close()
