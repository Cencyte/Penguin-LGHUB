#!/usr/bin/env python3
"""Bound native decompiler address space inside an explicitly owned service cgroup."""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import time

from dragonshard import atomic_json, GIB


def run(args):
    if not args.unit.startswith('dragonshard2-') or not 1<=args.limit_gib<=32:
        raise ValueError('Explicit owned unit and bounded limit required')
    group=subprocess.check_output(['systemctl','--user','show',args.unit,'-p','ControlGroup','--value'],text=True).strip()
    if not group:raise ValueError('No cgroup')
    root=Path('/sys/fs/cgroup')/group.lstrip('/');seen={};limit=int(args.limit_gib*GIB)
    while root.exists():
        for procfile in root.rglob('cgroup.procs'):
            try:pids=procfile.read_text().split()
            except OSError:continue
            for pid in pids:
                try:
                    p=Path('/proc')/pid
                    if (p/'comm').read_text().strip()!='decompile':continue
                    if group not in (p/'cgroup').read_text():continue
                    start=(p/'stat').read_text().rsplit(')',1)[1].split()[19]
                    key=pid+':'+start
                    if key in seen:continue
                    # prlimit targets only native helpers owned by this exact run.
                    current=resource.prlimit(int(pid),resource.RLIMIT_AS)
                    bounded=limit if current[1]==resource.RLIM_INFINITY else min(limit,current[1])
                    resource.prlimit(int(pid),resource.RLIMIT_AS,(bounded,bounded))
                    seen[key]=dict(pid=int(pid),start_ticks=start,address_space_limit_bytes=bounded)
                except ProcessLookupError:pass
                except FileNotFoundError:pass
        atomic_json(args.status,dict(state='watching',unit=args.unit,limit_gib=args.limit_gib,helpers=list(seen.values())))
        time.sleep(1)
    atomic_json(args.status,dict(state='unit_exited',unit=args.unit,helpers=list(seen.values())))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('unit');parser.add_argument('status',type=Path)
    parser.add_argument('--limit-gib',type=float,default=12);run(parser.parse_args())
