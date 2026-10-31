#!/usr/bin/env python3
"""G HUB profile acceptance under private headless KWin, with a host observer.

Reuses the copied-prefix test preparation. Native fixtures and KWin are outside
the agent's nested user namespace. No live desktop or physical devices exposed.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import native_profiles as bridge
import run_native_profiles_test as base

SRC=Path(__file__).resolve().parent


def outer(lab):
    assert (lab/'.native-profiles-disposable').is_file()
    assert not os.environ.get('DISPLAY') and not Path('/dev/input').exists()
    runtime=Path('/tmp/penguin-profile-probe/run'); runtime.mkdir(mode=0o700,parents=True)
    (lab/'kwin-config').mkdir(exist_ok=True)
    Path('/tmp/.X11-unix').mkdir(exist_ok=True)
    Path('/tmp/.X11-unix').chmod(0o1777)
    source=SRC.parent/'packaging/wayland_profile_probe.cpp'
    flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','Qt6Widgets','Qt6DBus'],text=True))
    moc=subprocess.check_output(['pkg-config','--variable=libexecdir','Qt6Core'],text=True).strip()+'/moc'
    subprocess.run([moc,str(source),'-o',str(lab/'wayland_profile_probe.moc')],check=True)
    subprocess.run(['g++','-fPIC','-I',str(lab),str(source),'-o',str(lab/'native-a'),*flags],check=True)
    for name in ('native-b','lghub.exe','unmatched'):
        shutil.copy2(lab/'native-a',lab/name)
    script=lab/'wayland-session'
    script.write_text('#!/bin/sh\nexec /usr/bin/python3 '+shlex.quote(str(Path(__file__).resolve()))+
                      ' --lab '+shlex.quote(str(lab))+' --inner\n');script.chmod(0o700)
    env=dict(os.environ,XDG_RUNTIME_DIR=str(runtime),XDG_CONFIG_HOME=str(lab/'kwin-config'),
             XDG_SESSION_TYPE='wayland',XDG_CURRENT_DESKTOP='KDE',QT_QPA_PLATFORM='offscreen',
             KWIN_COMPOSE='Q',LIBGL_ALWAYS_SOFTWARE='1',GALLIUM_DRIVER='llvmpipe',
             __EGL_VENDOR_LIBRARY_FILENAMES='/usr/share/glvnd/egl_vendor.d/50_mesa.json',
             __GLX_VENDOR_LIBRARY_NAME='mesa',PENGUIN_PRIVATE_PROFILE_PROBE='1',
             QT_NO_XDG_DESKTOP_PORTAL='1',QT_FORCE_STDERR_LOGGING='1')
    (lab/'wayland-results.json').unlink(missing_ok=True)
    (lab/'orphan-plugin').unlink(missing_ok=True)
    with (lab/'kwin.log').open('w') as log:
        subprocess.run(['dbus-run-session','--','kwin_wayland_wrapper','--virtual','--xwayland',
                        '--no-lockscreen','--no-global-shortcuts','--no-kactivities',
                        '--exit-with-session',str(script)],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=160)
    result=json.loads((lab/'wayland-results.json').read_text())
    print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 1


def agent_child(lab):
    config={'native_profiles':1,'kind':'penguin-desktop-runtime'}
    remote=bridge.prepare(config,lab)
    env=dict(os.environ,**json.loads((lab/'config.json').read_text())['environment'])
    env.update(WINEPREFIX=str(lab/'prefix'),WINEDEBUG='-all',LP_NUM_THREADS='2',**remote.mailbox.environment())
    env.pop('WAYLAND_DISPLAY',None)
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop)
    with (lab/'wayland-agent.log').open('w') as log:
        agent=subprocess.Popen(['wine',str(lab/'prefix/drive_c/Program Files/LGHUB/lghub_agent.exe')],
                               env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            end=time.monotonic()+90
            while not stopped:
                ready=subprocess.run(['node','--experimental-websocket',str(SRC/'agent_snapshot.cjs')],
                                     env=env,capture_output=True,text=True,timeout=12)
                if ready.returncode==0:break
                if agent.poll() is not None or time.monotonic()>end:
                    raise RuntimeError('G HUB readiness failed: '+ready.stderr)
                time.sleep(.5)
            if not stopped:
                remote.start()
                (lab/'agent-child-ready').write_text('ready')
            while not stopped and agent.poll() is None and not (lab/'agent-child-stop').exists():
                remote.check();time.sleep(.2)
        finally:
            remote.close()
            subprocess.run(['wineserver','-k'],env=env,timeout=10,check=False)
            subprocess.run(['wineserver','-w'],env=env,timeout=10,check=False)
            agent.wait(timeout=10)
    return 0


def inner(lab):
    assert os.environ.get('PENGUIN_PRIVATE_PROFILE_PROBE')=='1'
    assert os.environ.get('XDG_RUNTIME_DIR')=='/tmp/penguin-profile-probe/run'
    assert not Path('/dev/input').exists()
    results={'passed':False,'checks':[],'physical_devices':False,'live_desktop_touched':False,
             'scope':'Native Wayland/Xwayland focus -> host observer -> child supervisor in separate user namespace -> actual G HUB profile policy'}
    children=[];logs=[];done=threading.Event();thread=None
    def launch(args,name,env=None):
        log=(lab/(name+'.log')).open('w');logs.append(log)
        child=subprocess.Popen(list(map(str,args)),env=env,stdout=log,stderr=subprocess.STDOUT)
        children.append(child);return child
    def call(path,verb='GET',payload=None):
        msg=dict(path=path,verb=verb)
        if payload is not None:msg['payload']=payload
        r=subprocess.run(['node','--experimental-websocket',str(SRC/'profile_api_probe.cjs'),json.dumps(msg)],
                         capture_output=True,text=True,timeout=7)
        if r.returncode:raise RuntimeError(r.stderr+r.stdout)
        return json.loads(r.stdout).get('payload',{})
    def expect(case,profile):
        end=time.monotonic()+8
        while True:
            active=call('/profile/active')['id']
            if active==profile:break
            if time.monotonic()>end:raise AssertionError((case,active,profile))
            time.sleep(.1)
        results['checks'].append(dict(case=case,passed=True,profile=active))
    def window(name,platform='wayland'):
        label=name+'-'+str(len(children))
        child=launch([lab/name,'--window','org.penguin.'+name],name+'-'+str(len(children)),
                     dict(os.environ,QT_QPA_PLATFORM=platform))
        end=time.monotonic()+15
        while '"kind":"active"' not in (lab/(label+'.log')).read_text(errors='replace'):
            if child.poll() is not None or time.monotonic()>end:
                raise RuntimeError(f'Fixture {label} failed to activate; exit={child.poll()}')
            time.sleep(.1)
        time.sleep(.2)
        return child
    host=bridge.HostSession({'native_profiles':1,'kind':'penguin-desktop-runtime'},lab)
    try:
        def service():
            while not done.is_set():
                host.poll();done.wait(.1)
        thread=threading.Thread(target=service);thread.start()
        (lab/'agent-child-ready').unlink(missing_ok=True)
        (lab/'agent-child-stop').unlink(missing_ok=True)
        child=launch(['bwrap','--bind','/','/','--dev-bind','/dev','/dev','--proc','/proc',
                      '--unshare-user','--die-with-parent','--',sys.executable,str(Path(__file__).resolve()),
                      '--lab',lab,'--agent-child'],'agent-supervisor',dict(os.environ,**host.environment()))
        end=time.monotonic()+100
        while not (lab/'agent-child-ready').exists():
            if child.poll() is not None or time.monotonic()>end:raise RuntimeError('Child startup failed')
            time.sleep(.1)
        a=window('native-a');expect('native Wayland application A',base.IDS[0])
        b=window('native-b');expect('native Wayland application B',base.IDS[1])
        b.terminate();b.wait(timeout=5);expect('return to native application A',base.IDS[0])
        b=window('native-b','xcb');expect('Xwayland application B',base.IDS[1])
        call('/profiles/auto_switching','SET',{'@type':'type.googleapis.com/google.protobuf.BoolValue','value':False})
        a2=window('native-a');time.sleep(.5);expect('automatic-off preserves selected profile',base.IDS[1])
        call('/profiles/auto_switching','SET',{'@type':'type.googleapis.com/google.protobuf.BoolValue','value':True})
        expect('automatic-on resumes native foreground',base.IDS[0])
        from profile_dbus import Bus
        bus=Bus()
        saver=launch([lab/'unmatched','--screensaver'],'screen-lock-protocol')
        end=time.monotonic()+5
        while host.observer.source.lock_owner is None:
            if saver.poll() is not None or time.monotonic()>end:
                raise RuntimeError('Private screen-lock fixture unavailable')
            time.sleep(.1)
        bus.call('org.freedesktop.ScreenSaver','/ScreenSaver','org.freedesktop.ScreenSaver','SetActive',True)
        end=time.monotonic()+3
        while not host.observer.source.locked:
            if time.monotonic()>end:raise RuntimeError('Lock signal was not observed')
            time.sleep(.05)
        locked_window=window('native-b');time.sleep(.6)
        expect('screen-lock protocol pauses profile switching',base.IDS[0])
        bus.call('org.freedesktop.ScreenSaver','/ScreenSaver','org.freedesktop.ScreenSaver','SetActive',False)
        expect('screen-unlock protocol resumes current foreground',base.IDS[1])
        locked_window.terminate();locked_window.wait(timeout=5)
        expect('return after unlock',base.IDS[0])
        own=window('lghub.exe');time.sleep(.5);expect('G HUB own window preserves profile',base.IDS[0])
        other=window('unmatched');expect('unmatched native application uses Desktop',base.DESKTOP)
        other.terminate();other.wait(timeout=5);own.terminate();own.wait(timeout=5)
        expect('return from unmatched application',base.IDS[0])
        # Remove only this observer's ephemeral script to exercise recovery.
        # The receiver exposes its backend only to this private harness.
        source=host.observer.source
        bus.call('org.kde.KWin','/Scripting','org.kde.kwin.Scripting','unloadScript',source.plugin)
        time.sleep(4.5)
        recovered=window('native-b');expect('missing KWin subscription recovers',base.IDS[1])
        for app in (a,b,a2,recovered):
            if app.poll() is None:app.terminate();app.wait(timeout=5)
        expect('empty foreground returns Desktop',base.DESKTOP)
        (lab/'agent-child-stop').write_text('stop')
        child.wait(timeout=20)
        if child.returncode!=0:raise RuntimeError('Child shutdown failed')
        assert host.observer.closed
        assert host.observer.mailbox.status()[1]=='stopped'
        results['checks'].append(dict(case='host shutdown and agent acknowledgement',passed=True))
        orphan=launch([sys.executable,Path(__file__).resolve(),'--lab',lab,'--orphan-probe'],'orphan-receiver')
        end=time.monotonic()+6
        while not (lab/'orphan-plugin').exists():
            if orphan.poll() is not None or time.monotonic()>end:raise RuntimeError('Orphan fixture did not start')
            time.sleep(.1)
        plugin=(lab/'orphan-plugin').read_text()
        assert bus.call('org.kde.KWin','/Scripting','org.kde.kwin.Scripting','isScriptLoaded',plugin)==(True,)
        orphan.kill();orphan.wait(timeout=5)
        end=time.monotonic()+4
        while bus.call('org.kde.KWin','/Scripting','org.kde.kwin.Scripting','isScriptLoaded',plugin)!=(False,):
            if time.monotonic()>end:raise RuntimeError('Orphan KWin script did not remove itself')
            time.sleep(.1)
        results['checks'].append(dict(case='observer crash removes temporary KWin script',passed=True))
        bus.close()
        results['passed']=True
    except Exception as exc:
        results['error']=str(exc)
        results['host_error']=host.error
        results['adapter_status']=host.observer.mailbox.status()
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=15)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        done.set()
        if thread:thread.join(timeout=6)
        host.__exit__(None,None,None)
        for log in logs:log.close()
        results['passed_checks']=sum(x['passed'] for x in results['checks'])
        (lab/'wayland-results.json').write_text(json.dumps(results,indent=2)+'\n')
    return 0  # Never let KWin wrapper restart/overwrite a failed test session.


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path);p.add_argument('--lab',type=Path,required=True)
    p.add_argument('--worker',action='store_true');p.add_argument('--inner',action='store_true')
    p.add_argument('--agent-child',action='store_true')
    p.add_argument('--orphan-probe',action='store_true')
    a=p.parse_args();os.umask(0o077)
    if a.orphan_probe:
        assert os.environ.get('PENGUIN_PRIVATE_PROFILE_PROBE')=='1'
        from profile_kwin import KWin
        directory=a.lab/'orphan-script';directory.mkdir(exist_ok=True)
        source=KWin(directory)
        while not source.loaded:source.foreground();source.wait(.1)
        (a.lab/'orphan-plugin').write_text(source.plugin)
        while True:source.foreground();source.wait(.1)
    if a.agent_child:sys.exit(agent_child(a.lab))
    if a.inner:sys.exit(inner(a.lab))
    if a.worker:sys.exit(outer(a.lab))
    if not a.source:p.error('--source is required')
    sys.exit(base.prepare(a.source.resolve(),a.lab.resolve(),Path(__file__).resolve()))
