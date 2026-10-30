#!/usr/bin/env python3
"""Inspect the production graphics/audio imports without windows, sound or input."""
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import time

import desktop_runtime as desktop
import linux_runtime as linux


def egl_probe():
    egl=C.CDLL('libEGL.so.1')
    egl.eglGetProcAddress.argtypes=[C.c_char_p];egl.eglGetProcAddress.restype=C.c_void_p
    def extension(name, result, *args):
        pointer=egl.eglGetProcAddress(name.encode())
        return C.CFUNCTYPE(result,*args)(pointer) if pointer else None
    platform=extension('eglGetPlatformDisplayEXT',C.c_void_p,C.c_uint,C.c_void_p,C.POINTER(C.c_int))
    if not platform:raise RuntimeError('EGL platform extension unavailable')
    egl.eglInitialize.argtypes=[C.c_void_p,C.POINTER(C.c_int),C.POINTER(C.c_int)]
    egl.eglTerminate.argtypes=[C.c_void_p]
    egl.eglQueryString.argtypes=[C.c_void_p,C.c_int];egl.eglQueryString.restype=C.c_char_p
    egl.eglBindAPI.argtypes=[C.c_uint]
    egl.eglChooseConfig.argtypes=[C.c_void_p,C.POINTER(C.c_int),C.POINTER(C.c_void_p),C.c_int,C.POINTER(C.c_int)]
    egl.eglCreatePbufferSurface.argtypes=[C.c_void_p,C.c_void_p,C.POINTER(C.c_int)];egl.eglCreatePbufferSurface.restype=C.c_void_p
    egl.eglCreateContext.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.POINTER(C.c_int)];egl.eglCreateContext.restype=C.c_void_p
    egl.eglMakeCurrent.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p]
    egl.eglDestroyContext.argtypes=[C.c_void_p,C.c_void_p]
    egl.eglDestroySurface.argtypes=[C.c_void_p,C.c_void_p]
    platforms=[]
    query=extension('eglQueryDevicesEXT',C.c_uint,C.c_int,C.POINTER(C.c_void_p),C.POINTER(C.c_int))
    if query:
        devices=(C.c_void_p*16)();count=C.c_int()
        if query(16,devices,C.byref(count)):
            platforms += [('device',0x313f,devices[i]) for i in range(min(count.value,16))]
    platforms.append(('surfaceless',0x31dd,None))
    records=[]
    for kind,enum,device in platforms:
        display=platform(enum,device,None);major=C.c_int();minor=C.c_int()
        if not display or not egl.eglInitialize(display,C.byref(major),C.byref(minor)):continue
        surface=context=None
        try:
            record={'platform':kind,'vendor':egl.eglQueryString(display,0x3053).decode(),'version':f'{major.value}.{minor.value}'}
            attributes=(C.c_int*9)(0x3033,1,0x3040,4,0x3024,8,0x3023,8,0x3038)
            config=C.c_void_p();count=C.c_int()
            if not egl.eglChooseConfig(display,attributes,C.byref(config),1,C.byref(count)) or not count.value:
                raise RuntimeError('No offscreen GLES2 configuration')
            egl.eglBindAPI(0x30a0)
            surface=egl.eglCreatePbufferSurface(display,config,(C.c_int*5)(0x3057,1,0x3056,1,0x3038))
            context=egl.eglCreateContext(display,config,None,(C.c_int*3)(0x3098,2,0x3038))
            if not surface or not context or not egl.eglMakeCurrent(display,surface,surface,context):
                raise RuntimeError('Offscreen context could not be made current')
            get_string=extension('glGetString',C.c_char_p,C.c_uint)
            if not get_string or not get_string(0x1f01):raise RuntimeError('No renderer string from the current context')
            record['renderer']=get_string(0x1f01).decode()
            record['software']=any(n in record['renderer'].lower() for n in ('llvmpipe','softpipe','swrast'))
            records.append(record)
        finally:
            egl.eglMakeCurrent(display,None,None,None)
            if context:egl.eglDestroyContext(display,context)
            if surface:egl.eglDestroySurface(display,surface)
            egl.eglTerminate(display)
    if not records:raise RuntimeError('No usable offscreen EGL display')
    return {'contexts':records,'hardware_context':any(not r['software'] for r in records)}


def audio_probe():
    pulse=C.CDLL('libpulse.so.0')
    pulse.pa_mainloop_new.restype=C.c_void_p
    pulse.pa_mainloop_get_api.argtypes=[C.c_void_p];pulse.pa_mainloop_get_api.restype=C.c_void_p
    pulse.pa_context_new.argtypes=[C.c_void_p,C.c_char_p];pulse.pa_context_new.restype=C.c_void_p
    pulse.pa_context_connect.argtypes=[C.c_void_p,C.c_char_p,C.c_int,C.c_void_p]
    pulse.pa_context_get_state.argtypes=[C.c_void_p]
    pulse.pa_mainloop_iterate.argtypes=[C.c_void_p,C.c_int,C.POINTER(C.c_int)]
    pulse.pa_context_disconnect.argtypes=[C.c_void_p];pulse.pa_context_unref.argtypes=[C.c_void_p]
    pulse.pa_mainloop_free.argtypes=[C.c_void_p]
    loop=pulse.pa_mainloop_new()
    if not loop:raise RuntimeError('Audio mainloop allocation failed')
    context=pulse.pa_context_new(pulse.pa_mainloop_get_api(loop),b'Penguin-LGHUB readiness check')
    if not context:pulse.pa_mainloop_free(loop);raise RuntimeError('Audio context allocation failed')
    state=0
    try:
        # NOAUTOSPAWN: inspect the existing service, never start or reconfigure it.
        result=pulse.pa_context_connect(context,None,1,None)
        deadline=time.monotonic()+4
        while result==0 and time.monotonic()<deadline:
            pulse.pa_mainloop_iterate(loop,0,None);state=pulse.pa_context_get_state(context)
            if state in (4,5,6):break
            time.sleep(.02)
        return {'pulse_connected':state==4,'state':state,'connect_status':result,'sound_played':False}
    finally:
        pulse.pa_context_disconnect(context);pulse.pa_context_unref(context);pulse.pa_mainloop_free(loop)


def worker(config):
    root,wine=(Path(config[k]) for k in ('linux_runtime','wine'))
    os.environ['LD_LIBRARY_PATH']=':'.join(filter(None,(os.environ.get('LD_LIBRARY_PATH'),linux.library_path(root,wine))))
    report={'schema':1,'window_created':False,'input_injected':False,'sound_played':False,
            'end_to_end_qualified':False,'warnings':[]}
    for name,probe in (('graphics',egl_probe),('audio',audio_probe)):
        try:report[name]=probe()
        except (OSError,RuntimeError) as error:
            report[name]={'error':str(error)};report['warnings'].append(name+': '+str(error))
    if not report['graphics'].get('hardware_context'):report['warnings'].append('Hardware rendering not confirmed; inspect the renderer and test G HUB on this desktop.')
    if not report['audio'].get('pulse_connected'):report['warnings'].append('Existing PulseAudio/PipeWire-pulse connection unavailable; audio integration needs investigation.')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args()
    config=json.loads(a.config.read_text())
    if a.worker:
        report=worker(config);a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));return
    desktop.verify(config,a.config.parent)
    a.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    command=desktop.container_command(config,a.config.parent,
        ['/usr/bin/python3',str(Path(__file__).resolve()),'--worker','--config',str(a.config),'--output',str(a.output)])
    command[command.index('--'):command.index('--')]=[f'--filesystem={a.output.parent}:rw']
    env=desktop.session_environment(os.environ,True)
    for key in ('DISPLAY','WAYLAND_DISPLAY'):env.pop(key,None)
    subprocess.run(command,env=env,check=True,timeout=90)

if __name__=='__main__':
    os.umask(0o077);main()
