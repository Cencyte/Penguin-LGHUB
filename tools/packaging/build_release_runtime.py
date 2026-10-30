#!/usr/bin/env python3
"""Build every project Wine replacement from public source in the pinned SDK.

The historical runtime remains valid and untouched. New source builds carry a
separate recipe receipt; their hashes need not match the old host toolchain.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from windows_media_acquire import ROOT, digest_file, check_asset, download_asset, external_path
import linux_sdk
import toolchain
import wine_runtime


def recipe():
    files = [ROOT / ('tools/packaging/'+n) for n in
             ('build_release_runtime.py','toolchain.py','linux_sdk.py','linux_runtime.py','wine_runtime.py')]
    files += [ROOT / 'tools/wine/apply_patch_series.py', toolchain.LOCK, linux_sdk.LOCK,
             wine_runtime.LOCK, *sorted((ROOT / 'patches/wine').glob('00*.patch'))]
    files += sorted((ROOT / 'tools/wine').glob('*.[ch]'))
    files += sorted((ROOT / 'tools/wine').glob('*.cpp'))
    files += sorted((ROOT / 'tools/wine').glob('*.h'))
    files += sorted((ROOT / 'tools/wine').glob('*.def'))
    files += [ROOT / 'tools/packaging/clean_prefix_probe.cpp']
    return {str(p.relative_to(ROOT)):digest_file(p)['sha256'] for p in files}


def recipe_digest():
    return hashlib.sha256(json.dumps(recipe(),sort_keys=True).encode()).hexdigest()


def run(args, cwd=None):
    print('+ '+ ' '.join(map(str,args)),flush=True)
    subprocess.run(list(map(str,args)),cwd=cwd,check=True)


def worker(work, jobs):
    if os.environ.get('PENGUIN_TOOLCHAIN') != '1':
        raise ValueError('Build worker requires the isolated project toolchain')
    source_spec=json.loads(linux_sdk.LOCK.read_text())['wine_source']
    archive=work/'cache'/source_spec['local_name'];check_asset(archive,source_spec)
    buildwork=work/'source-build'
    buildwork.mkdir(exist_ok=True)
    if not (buildwork/'source').exists():
        linux_sdk.extract_root(archive,buildwork/'source',512*1024**2)
    source=buildwork/'source/wine-wine-10.20'
    sys.path.insert(0,str(ROOT/'tools/wine'))
    from apply_patch_series import apply_series
    apply_series(source,sorted((ROOT/'patches/wine').glob('00*.patch')))
    run(['./tools/make_requests'],source);run(['./tools/make_specfiles'],source)
    build=buildwork/'build';build.mkdir(exist_ok=True)
    run([source/'configure','--prefix=/usr','--libdir=/usr/lib','--enable-win64',
         '--without-wayland','--disable-tests'],build)
    lock=json.loads(wine_runtime.LOCK.read_text())
    targets={name.split('/',2)[2] for name in lock['patches']}
    targets.update(('dlls/winedmo/winedmo.so','dlls/wpcap/wpcap.so'))
    run(['make','-j'+str(jobs),*sorted(targets)],build)
    output=work/'built-wine'
    if not output.exists():
        shutil.copytree('/opt/wine',output,symlinks=True)
        (output/'provenance/source-build-incomplete').write_text('private source build\n')
    elif not (output/'provenance/source-build-incomplete').is_file():
        raise ValueError('Existing build output has no resumable build marker')
    # The untouched Arch package is only the base. Every changed file below is
    # built from the public 45-patch tree, including the two restore/start guards.
    files={}
    for name,spec in lock['patches'].items():
        relative=name.split('/',2)[2];dst=output/spec['target']
        shutil.copyfile(build/relative,dst);files[spec['target']]=digest_file(dst)['sha256']
        dst.chmod(0o755 if spec['target']=='usr/bin/wineserver' else 0o644)
    for name in ('winedmo','wpcap'):
        target=f'usr/lib/wine/x86_64-unix/{name}.so'
        shutil.copyfile(build/f'dlls/{name}/{name}.so',output/target)
        files[target]=digest_file(output/target)['sha256']
    out=output/'compatibility';(out/'system_tray').mkdir(parents=True,exist_ok=True)
    # The pinned MinGW 12 SDK omits this WinRT header. Generate it from Wine's
    # LGPL IDL, already compiled by WIDL, without importing proprietary headers.
    headers=buildwork/'compat-headers';headers.mkdir(exist_ok=True)
    shutil.copyfile(build/'include/weakreference.h',headers/'weakreference.h')
    cc='x86_64-w64-mingw32-gcc';cxx='x86_64-w64-mingw32-g++';src=ROOT/'tools/wine'
    common=['-shared','-O2','-static','-Wno-cast-function-type','-D_WIN32_WINNT=0x0a00','-I'+str(headers)]
    agile_def=buildwork/'agile-reference.def'
    agile_def.write_text('LIBRARY combase.dll\nEXPORTS\nRoGetAgileReference\n')
    agile_lib=buildwork/'libagile-reference.a'
    run(['x86_64-w64-mingw32-dlltool','-d',agile_def,'-l',agile_lib])
    commands=[
      [cxx,*common,src/'api_information_proxy.cpp','-o',out/'api_information_proxy.dll','-lruntimeobject','-lole32','-luuid'],
      [cxx,*common,src/'core_application_experiment.cpp',agile_lib,'-o',out/'core_application_experiment.dll','-lruntimeobject','-lole32','-luuid'],
      [cc,'-shared','-O2',src/'sensor_monitor_unavailable.c','-o',out/'mfsensorgroup.dll'],
      [cxx,*common,'-std=c++17',src/'dcomp_experiment.cpp',src/'dcomp_clock.cpp',src/'dcomp_experiment.def','-o',out/'system_tray/dcomp.dll','-lole32','-lgdi32','-luser32','-ld3d11','-ldxguid','-luuid','-lntdll'],
      [cxx,*common,'-std=c++17',src/'dispatcher_queue_experiment.cpp',src/'tray_popup_anchor.cpp',src/'dispatcher_queue_experiment.def',agile_lib,'-o',out/'system_tray/ghub_dispatcher_queue.dll','-lruntimeobject','-lole32','-luuid','-luser32','-lcomctl32']]
    adapter=buildwork/'coremessaging.def'
    adapter.write_text((src/'coremessaging_xp_adapter.def').read_text().replace('CreateDispatcherQueueController=ghub_wine_coremessaging.', 'CreateDispatcherQueueController=ghub_dispatcher_queue.').replace('DllGetActivationFactory=ghub_wine_coremessaging.', 'DllGetActivationFactory=ghub_dispatcher_queue.'))
    commands += [[cc,'-shared','-O2',src/'coremessaging_xp_adapter.c',adapter,'-o',out/'system_tray/CoreMessaging.dll']]
    for cmd in commands:run(cmd)
    shutil.copyfile(output/'usr/lib/wine/x86_64-windows/coremessaging.dll',out/'system_tray/ghub_wine_coremessaging.dll')
    supplements={}
    for name,spec in lock['supplements'].items():
        spec={**spec,'sha256':digest_file(output/spec['target'])['sha256']}
        supplements[name]=spec;files[spec['target']]=spec['sha256']
    probe=output/'tools/clean_prefix_probe.exe';probe.parent.mkdir(exist_ok=True)
    run([cxx,'-O2','-static',ROOT/'tools/packaging/clean_prefix_probe.cpp','-o',probe,'-lruntimeobject','-lole32','-luuid','-ladvapi32'])
    files['tools/clean_prefix_probe.exe']=digest_file(probe)['sha256']
    (output/'licenses').mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'packaging/licenses/Wine-LGPL-2.1.txt',output/'licenses/Wine-LGPL-2.1.txt')
    (output/'provenance').mkdir(exist_ok=True)
    receipt={'recipe_sha256':recipe_digest(),'sources':recipe(),'files':files,'patch_count':45,
             'headless_acquisition_only':False,'runtime_acceptance_pending':True}
    (output/'provenance/source-build.json').write_text(json.dumps(receipt,indent=2)+'\n')
    audit=wine_runtime.audit_unix(output)
    (output/'provenance/unix-dependencies.json').write_text(json.dumps(audit,indent=2)+'\n')
    report={'schema':1,'kind':'penguin-private-wine-runtime','wine_version':'10.20',
            'lock_sha256':digest_file(wine_runtime.LOCK)['sha256'],'source_build':receipt,
            'supplements':supplements,'files':wine_runtime.inventory(output),'host_wine_required':False,
            'source_release_audit_complete':False}
    (output/'runtime.json').write_text(json.dumps(report,indent=2)+'\n')
    wine_runtime.verify_runtime(output)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--toolchain',type=Path)
    p.add_argument('--jobs',type=int,default=4);p.add_argument('--worker',action='store_true')
    a=p.parse_args()
    if not 1<=a.jobs<=16:p.error('Use 1–16 build jobs')
    if a.worker:worker(a.work,a.jobs);return
    work=external_path(a.work);root=external_path(a.toolchain);toolchain.verify(root)
    spec=json.loads(linux_sdk.LOCK.read_text())['wine_source']
    download_asset(work/'cache',spec,validate=linux_sdk.pinned_url)
    run(toolchain.command(root,work,['python3','/project/tools/packaging/build_release_runtime.py',
                                   '--worker','--work','/work','--jobs',str(a.jobs)]))

if __name__=='__main__':
    os.umask(0o077);main()
