#!/usr/bin/env python3
"""Check Wine keyboard output in a native X client on a private Xvfb server.

Run inside run_private_build.py with a stopped, disposable prefix and network
namespace. Physical HID drivers stay disabled. No live desktop input is used.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import time

from tested_desktop_launch import prefix_processes


def evaluate_events(native_text, wine_text, wine_target, expect_blocked):
    events=[line.split()[1:] for line in native_text.splitlines() if line.startswith('KEY ')]
    # The control chord must carry ControlMask, then release it. Also distinguish
    # dedicated navigation and Return from their keypad counterparts.
    symbols=[0x5b,0x5d,0x30,0xffff,0xffe4,0x61,0xffe4,0xff51,0x61,0xff0d,0xff8d,0xffc9]
    expected=[]
    for index,symbol in enumerate(symbols):
        directions=['down'] if index==4 else ['up'] if index==6 else ['down','up']
        state=4 if index in (5,6) else 0
        expected.extend((direction,format(symbol,'x'),format(state,'x')) for direction in directions)
    actual=[tuple(event) for event in events]
    wine_actual=[tuple(int(value,16) for value in line.split()[1:])
                 for line in wine_text.splitlines() if line.startswith('WINE_KEY ')]
    wine_expected=[]
    for index,(vk,scan) in enumerate([(0xdb,0x1a),(0xdd,0x1b),(0x30,0xb),(0x2e,0x153),
            (0x11,0x11d),(0x41,0x1e),(0x11,0x11d),(0x25,0x14b),(0x41,0x1e),
            (0xd,0x1c),(0xd,0x11c),(0x7b,0x58)]):
        messages=[0x100] if index==4 else [0x101] if index==6 else [0x100,0x101]
        wine_expected.extend((message,vk,(scan<<16)|1|(0xc0000000 if message==0x101 else 0))
                             for message in messages)
    native_ok=not actual if expect_blocked or wine_target else actual==expected
    wine_ok=wine_actual==wine_expected if wine_target else not wine_actual
    return dict(events_passed=native_ok and wine_ok,expected=expected,actual=actual,
                wine_expected=wine_expected if wine_target else [],wine_actual=wine_actual)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--expect-blocked',action='store_true')
    parser.add_argument('--disable-bridge',action='store_true')
    parser.add_argument('--other-process',action='store_true')
    parser.add_argument('--wine-target',action='store_true')
    args=parser.parse_args()
    prefix=Path(os.environ['WINEPREFIX']).resolve(strict=True)
    if not (prefix/'.overlay-trace-disposable').is_file() or prefix_processes(prefix):
        parser.error('A stopped disposable prefix is required')
    host_net=subprocess.check_output(['sudo','-n','readlink','/proc/1/ns/net'],text=True).strip()
    if os.readlink('/proc/self/ns/net')==host_net: parser.error('Private network namespace required')
    args.output.mkdir(parents=True,exist_ok=False)
    args.lab=args.lab.resolve(strict=True)
    xvfb=subprocess.Popen(['Xvfb','-displayfd','1','-screen','0','800x600x24','-nolisten','tcp'],
                          stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    receiver=None
    env=dict(os.environ)
    for name in ('DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS'): env.pop(name,None)
    try:
        with selectors.DefaultSelector() as poll:
            poll.register(xvfb.stdout,selectors.EVENT_READ)
            if not poll.select(10): raise TimeoutError('Private Xvfb startup')
        number=xvfb.stdout.readline().decode().strip()
        if not number.isdecimal(): raise RuntimeError('Invalid owned display')
        env.update(DISPLAY=':'+number,GHUB_HOST_INPUT_PRIVATE_TEST='1',WINE_GHUB_HOST_KEYBOARD='1',
                   WINEDEBUG='-all,err+all,trace+keyboard',
                   WINEDLLOVERRIDES='winemenubuilder.exe=d;winebus.sys=d;wineusb.sys=d;lghub_software_manager.exe=d')
        if args.disable_bridge: env.pop('WINE_GHUB_HOST_KEYBOARD')
        with (args.output/'wine.log').open('w') as log:
            subprocess.run(['wine','cmd','/c','exit'],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
            receiver=subprocess.Popen([str(args.lab/'host_keyboard_receiver')],env=env,
                                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            with selectors.DefaultSelector() as poll:
                poll.register(receiver.stdout,selectors.EVENT_READ)
                if not poll.select(10) or receiver.stdout.readline().strip()!='READY':
                    raise RuntimeError('Private receiver startup')
            executable='host_keyboard_probe.exe' if args.other_process else 'lghub_agent.exe'
            if args.wine_target:
                with subprocess.Popen(['wine',str(args.lab/executable),'--wine-target'],env=env,
                                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True) as result:
                    with selectors.DefaultSelector() as poll:
                        poll.register(result.stdout,selectors.EVENT_READ)
                        if not poll.select(20) or result.stdout.readline().strip()!='WINE_READY':
                            result.kill(); raise RuntimeError('Private Wine receiver startup')
                    # There is no window manager on this owned Xvfb. Establish
                    # actual X focus, rather than just Wine's foreground HWND.
                    window=subprocess.check_output(['xdotool','search','--onlyvisible','--name',
                        '^Private Wine keyboard receiver$'],env=env,text=True,timeout=5).strip().splitlines()
                    if len(window)!=1: result.kill(); raise RuntimeError('Ambiguous private receiver')
                    subprocess.run(['xdotool','windowfocus','--sync',window[0]],env=env,check=True,timeout=5)
                    output,_=result.communicate('\n',timeout=30); log.write(output)
            else:
                result=subprocess.run(['wine',str(args.lab/executable)],env=env,
                                      stdout=log,stderr=subprocess.STDOUT,timeout=30)
        time.sleep(.3)
        received,_=receiver.communicate('\n',timeout=5)
        (args.output/'native-events.txt').write_text(received)
        evidence=evaluate_events(received,(args.output/'wine.log').read_text(errors='replace'),
                                 args.wine_target,args.expect_blocked)
        passed=result.returncode==0 and evidence['events_passed']
        report=dict(passed=passed,expect_blocked=args.expect_blocked,probe_exit=result.returncode,
                    wine_target=args.wine_target,disable_bridge=args.disable_bridge,other_process=args.other_process,
                    **evidence,live_desktop_touched=False)
        (args.output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
        return 0 if passed else 1
    finally:
        if receiver and receiver.poll() is None: receiver.terminate(); receiver.wait(timeout=5)
        subprocess.run(['wineserver','-k'],env=env,timeout=15)
        subprocess.run(['wineserver','-w'],env=env,timeout=15)
        xvfb.terminate(); xvfb.wait(timeout=5)


if __name__=='__main__': raise SystemExit(main())
