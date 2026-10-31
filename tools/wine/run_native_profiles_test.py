#!/usr/bin/env python3
"""Native automatic profile integration test in a copied prefix and owned Xvfb.

Requires existing tools (bwrap, Xvfb, Openbox, Node with WebSocket, MinGW, cc/X11).
No packages are installed. --source is a stopped frozen legacy launch.json root.
--lab must be a new directory, or one created by this test. No live GUI/device use.
"""
import argparse
import hashlib
import json
import os
import pwd
from pathlib import Path
import selectors
import shutil
import sqlite3
import subprocess
import sys
import time

import native_profiles as bridge

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC.parent / 'packaging'))
from prepare_clean_prefix import sandbox

IDS = ('c0ffee00-0000-4000-8000-000000000001', 'c0ffee00-0000-4000-8000-000000000002')
DESKTOP = '420fd454-0c36-499d-bde4-146823b16147'


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def prepare(source, lab, worker_script=None):
    if lab == source or lab.is_relative_to(source) or source.is_relative_to(lab):
        raise ValueError('Keep lab separate from the frozen source')
    marker = lab / '.native-profiles-disposable'
    if lab.exists() and not marker.is_file():
        raise ValueError('Refusing an existing directory without this test marker')
    if not lab.exists():
        lab.mkdir(mode=0o700); marker.write_text('Private profile test\n')
        subprocess.run(['cp', '-a', '--reflink=auto', str(source/'prefix'), str(lab/'prefix')], check=True)
    for p in Path('/proc').iterdir():
        try:
            if b'WINEPREFIX='+os.fsencode(lab/'prefix') in (p/'environ').read_bytes().split(b'\0'):
                raise RuntimeError('Disposable prefix is running')
        except (OSError, NotADirectoryError):
            pass
    config = json.loads((source/'launch.json').read_text())
    (lab/'config.json').write_text(json.dumps(config))
    for name in ('home', 'logs', 'guard'):
        (lab/name).mkdir(exist_ok=True)
    cc = 'x86_64-w64-mingw32-gcc'
    subprocess.run([cc, '-shared', '-O2', '-Wall', '-Wextra', '-Werror', str(SRC/'sensor_monitor_unavailable.c'),
                    str(SRC/'profile_event_bridge.c'), '-o', str(lab/'mfsensorgroup.dll'), '-lbcrypt'], check=True)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(SRC/'profile_focus_fixture.c'), '-o', str(lab/'native-a'), '-lX11'], check=True)
    for name in ('native-b', 'unmatched', 'lghub.exe'):
        shutil.copyfile(lab/'native-a', lab/name); (lab/name).chmod(0o700)
    subprocess.run([cc, '-O2', '-Wall', '-Wextra', '-Werror', str(SRC/'profile_guard_fixture.c'), '-o', str(lab/'guard/lghub_agent.exe')], check=True)
    shutil.copyfile(lab/'mfsensorgroup.dll', lab/'guard/mfsensorgroup.dll')
    shutil.copyfile(lab/'mfsensorgroup.dll', lab/'prefix/drive_c/Program Files/LGHUB/mfsensorgroup.dll')
    username = pwd.getpwuid(os.getuid()).pw_name
    settings = lab/'prefix/drive_c/users'/username/'AppData/Local/LGHUB/settings.db'
    if not settings.is_file():
        raise ValueError('Source prefix has no G HUB settings for the current account')
    with sqlite3.connect(settings) as c:
        data = json.loads(c.execute('select file from data').fetchone()[0])
        for name, uid in zip(('native-a', 'native-b'), IDS):
            data['applications']['applications'] = [a for a in data['applications']['applications'] if a['applicationId'] != uid]
            data['profiles']['profiles'] = [p for p in data['profiles']['profiles'] if p['id'] != uid]
            data['applications']['applications'].append({'applicationId': uid, 'applicationPath': bridge.wine_path(lab/name), 'name': name, 'isCustom': True})
            data['profiles']['profiles'].append({'id': uid, 'applicationId': uid, 'name': name, 'activeForApplication': True})
        data['profiles'].pop('auto_switching', None)
        data['profiles/auto_switching/enabled'] = True
        data['profiles/auto_switching/migrated'] = True
        c.execute('update data set file=?', (json.dumps(data).encode(),))
    args = sandbox(lab, ['python3', str(worker_script or Path(__file__).resolve()), '--source', str(source), '--lab', str(lab), '--worker'])
    mounts = []
    for name, sha in config['sha256'].items():
        if not name.startswith('builds/'):
            continue
        p = source/name
        if '/x86_64-windows/' in name:
            target = Path('/usr/lib/wine/x86_64-windows')/p.name
        elif name.endswith('/server/wineserver'):
            target = Path('/usr/bin/wineserver')
        elif p.suffix == '.so':
            target = Path('/usr/lib/wine/x86_64-unix')/p.name
        else:
            continue
        if digest(p) != sha:
            raise ValueError('Frozen Wine component changed: '+name)
        mounts += ['--ro-bind', str(p), str(target)]
        if p.name in ('shcore.dll', 'windows.ui.dll', 'windows.storage.dll', 'threadpoolwinrt.dll'):
            mounts += ['--ro-bind', str(p), str(lab/'prefix/drive_c/windows/system32'/p.name)]
    at = args.index('--'); args[at:at] = mounts
    return subprocess.run(args, env={'PATH':'/usr/bin:/bin', 'HOME':str(lab/'home'),
                                    'LANG':'C.UTF-8', 'USER':username, 'LOGNAME':username,
                                    'PYTHONDONTWRITEBYTECODE':'1'}).returncode


def worker(lab):
    assert (lab/'.native-profiles-disposable').is_file()
    assert not os.environ.get('DISPLAY') and not Path('/dev/input').exists() and not Path('/dev/hidraw0').exists()
    env = dict(os.environ, **json.loads((lab/'config.json').read_text())['environment'])
    env.update(WINEPREFIX=str(lab/'prefix'), WINEDEBUG='-all', LIBGL_ALWAYS_SOFTWARE='1', LP_NUM_THREADS='2')
    children = []; streams = []; checks = []
    observer = bridge.ProfileObserver(lab/'logs')
    env.update(observer.mailbox.environment())
    def launch(args, name):
        f = (lab/(name+'.log')).open('w'); streams.append(f)
        p = subprocess.Popen(list(map(str,args)), env=env, stdout=f, stderr=subprocess.STDOUT)
        children.append(p); return p
    def call(path, verb='GET', payload=None):
        message = {'path':path, 'verb':verb}
        if payload is not None: message['payload'] = payload
        p = subprocess.run(['node', '--experimental-websocket', str(SRC/'profile_api_probe.cjs'), json.dumps(message)], env=env, capture_output=True, text=True, timeout=7)
        if p.returncode: raise RuntimeError('Agent API: '+p.stdout+p.stderr)
        return json.loads(p.stdout).get('payload', {})
    def active(): return call('/profile/active')['id']
    def expect(label, expected):
        deadline = time.monotonic()+6
        while True:
            actual = active()
            if actual == expected: break
            if time.monotonic() >= deadline:
                detail = {'case':label, 'ack':observer.mailbox.status(),
                          'mailbox_sequence':observer.mailbox.sequence,
                          'focus':call('/application/focus'), 'applications':call('/applications'),
                          'profiles':call('/profiles'), 'auto':call('/profiles/auto_switching')}
                (lab/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
                raise AssertionError((label, actual, expected, detail['ack']))
            time.sleep(.1)
        checks.append({'case':label,'passed':True,'profile':actual})
        print('PASS',label,flush=True)
    def focus(pid):
        windows = subprocess.check_output(['xdotool','search','--pid',str(pid)], env=env, text=True).splitlines()
        assert len(windows)==1, windows
        subprocess.run(['xdotool','windowactivate','--sync',windows[0]],env=env,check=True,timeout=5)
    try:
        Path('/tmp/.X11-unix').mkdir(exist_ok=True)
        f=(lab/'xvfb.log').open('w');streams.append(f)
        x=subprocess.Popen(['Xvfb','-displayfd','1','-screen','0','1000x700x24','-nolisten','tcp','-extension','GLX'],env=env,stdout=subprocess.PIPE,stderr=f);children.append(x)
        with selectors.DefaultSelector() as s:
            s.register(x.stdout,selectors.EVENT_READ);assert s.select(10)
        number=x.stdout.readline().decode().strip();assert number.isdecimal()
        env['DISPLAY']=':'+number;os.environ['DISPLAY']=env['DISPLAY']
        launch(['openbox','--sm-disable'],'openbox');time.sleep(.5)
        # This executable's name is correct, hash is not: pinned calls must not run.
        guard = launch(['wine',lab/'guard/lghub_agent.exe'],'hash-guard')
        guard.wait(timeout=45)
        assert observer.mailbox.status() == (0,'unsupported-agent'), observer.mailbox.status()
        checks.append({'case':'wrong executable hash refused','passed':True})
        observer.mailbox.path.with_suffix('.bin.status').unlink()
        agent=launch(['wine',lab/'prefix/drive_c/Program Files/LGHUB/lghub_agent.exe'],'agent')
        deadline=time.monotonic()+90
        while True:
            try:
                ready = subprocess.run(['node', '--experimental-websocket', str(SRC/'agent_snapshot.cjs')],
                                       env=env, capture_output=True, text=True, timeout=12)
                if ready.returncode:
                    raise RuntimeError('Waiting for all launcher readiness endpoints')
                break
            except RuntimeError:
                if agent.poll() is not None or time.monotonic()>deadline: raise
                time.sleep(1)
        observer.start()
        a=launch([lab/'native-a'],'native-a');b=launch([lab/'native-b'],'native-b');time.sleep(1)
        for index,(p,want) in enumerate([(a,IDS[0]),(b,IDS[1]),(a,IDS[0]),(b,IDS[1])]):
            focus(p.pid);expect('automatic native focus '+str(index+1),want)
        call('/profiles/auto_switching','SET',{'@type':'type.googleapis.com/google.protobuf.BoolValue','value':False})
        focus(a.pid);time.sleep(.7);expect('automatic-off holds profile',IDS[1])
        call('/profiles/auto_switching','SET',{'@type':'type.googleapis.com/google.protobuf.BoolValue','value':True})
        expect('automatic-on resumes current foreground',IDS[0])
        own=launch([lab/'lghub.exe'],'own-window');time.sleep(.6)
        focus(own.pid);time.sleep(.5);expect('G HUB window preserves previous application profile',IDS[0])
        unmatched=launch([lab/'unmatched'],'unmatched-window');time.sleep(.6)
        focus(unmatched.pid);expect('unmatched native application returns Desktop',DESKTOP)
        focus(a.pid);expect('return from unmatched application',IDS[0])
        # Pause only the observer, then tamper private test packets; G HUB must hold.
        observer.stop_event.set();observer.thread.join(timeout=3);assert not observer.thread.is_alive()
        box=observer.mailbox
        for label,patcher in [('wrong token',lambda p:p.__setitem__(slice(8,72),b'0'*64)),
                              ('stale timestamp',lambda p:p.__setitem__(slice(80,88),(1).to_bytes(8,'little')))]:
            box.sequence += 1
            path=bridge.wine_path(lab/'native-b').encode()
            wrong=bytearray(bridge.HEADER.pack(b'PGFOCUS1',box.token.encode(),box.sequence,
                            time.time_ns()//1000000,0x80000000|box.sequence,0,len(path))+path)
            patcher(wrong)
            temporary=box.path.with_suffix('.invalid');temporary.write_bytes(wrong);temporary.replace(box.path)
            time.sleep(.3);expect(label+' rejected',IDS[0])
            box.current=bridge.wine_path(lab/'native-a')
        box.write(bridge.wine_path(lab/'native-b'));expect('valid event after rejected packets',IDS[1])
        box.write(bridge.DESKTOP);expect('empty focus desktop fallback',DESKTOP)
        observer.check();box.close();time.sleep(.2)
        assert box.status() == (box.sequence,'stopped'),box.status()
        checks.append({'case':'adapter shutdown acknowledged','passed':True})
        (lab/'results.json').write_text(json.dumps({'checks':checks,'agent_sha256':digest(lab/'prefix/drive_c/Program Files/LGHUB/lghub_agent.exe')},indent=2)+'\n')
        print('PASS',len(checks),'integration checks; no physical input devices',flush=True)
    finally:
        observer.close()
        subprocess.run(['wineserver','-k'],env=env,timeout=10,check=False)
        for p in reversed(children):
            if p.poll() is None:p.terminate()
        for p in children:
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        for f in streams:f.close()
        observer.mailbox.cleanup()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--lab',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args();os.umask(0o077)
    if a.worker:worker(a.lab.resolve());return 0
    return prepare(a.source.resolve(strict=True),a.lab.resolve())


if __name__=='__main__':
    raise SystemExit(main())
