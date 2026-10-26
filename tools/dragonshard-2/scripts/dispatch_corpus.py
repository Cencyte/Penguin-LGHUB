#!/usr/bin/env python3
"""Reclassify every computed-transfer site and retain its unresolved runtime dependencies."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

from campaign import available
from dragonshard import HERE, GIB, atomic_json, digest
from static_evidence import indirect, PE


PRIORITY = {'processor_trap': 0, 'named_import': 1, 'static_candidate': 2,
            'object_dispatch_slot': 3, 'runtime_call_result': 4, 'runtime_storage': 5,
            'runtime_register_or_argument': 6, 'runtime_dataflow': 7, 'no_high_slice': 8}


def classify(row):
    if row.get('import_slots'): return 'named_import'
    if row.get('candidates'): return 'static_candidate'
    if row.get('dispatch_slot') is not None: return 'object_dispatch_slot'
    dependencies = row.get('dependencies', {})
    if any(op in dependencies.get('operations', {}) for op in ('CALL','CALLIND')):
        return 'runtime_call_result'
    origins = dependencies.get('origins', [])
    if any(r.get('space') == 'ram' for r in origins): return 'runtime_storage'
    if any(r.get('space') == 'register' for r in origins): return 'runtime_register_or_argument'
    return 'runtime_dataflow'


def trap_kind(instruction, data):
    if instruction == 'INT3' and data[:1] == b'\xcc': return 'breakpoint'
    if instruction == 'INT1' and data[:1] == b'\xf1': return 'debug_exception'
    if instruction.startswith('INT ') and data[:1] == b'\xcd':
        return 'fast_fail' if data[1:2] == b'\x29' else 'software_interrupt'
    return None


def index(queue, catalog, root):
    path=root/'dispatch.sqlite.partial'
    db=sqlite3.connect(path)
    db.executescript('''PRAGMA journal_mode=OFF; PRAGMA temp_store=MEMORY; PRAGMA max_page_count=262144;
      CREATE TABLE programs(sha256 TEXT PRIMARY KEY,name TEXT,binary_path TEXT,snapshot_id TEXT);
      CREATE TABLE sites(sha256 TEXT,address TEXT,function_address TEXT,instruction TEXT,
        instruction_bytes TEXT,trap TEXT,classification TEXT,priority INTEGER,
        has_high_slice INTEGER DEFAULT 0,runtime_target_proven INTEGER DEFAULT 0,
        PRIMARY KEY(sha256,address));
      CREATE TABLE evidence(sha256 TEXT,address TEXT,source TEXT,payload TEXT);
      CREATE INDEX evidence_site ON evidence(sha256,address);
      CREATE TABLE sources(path TEXT PRIMARY KEY,sha256 TEXT);
      CREATE INDEX site_class ON sites(classification,sha256);''')
    for job in queue['jobs'].values():
        if job['kind']!='gaps': continue
        p=job['program'];sha=p['sha256'];pe=PE(p['binary_path'],sha)
        db.execute('INSERT INTO programs VALUES(?,?,?,?)',(sha,p['name'],p['binary_path'],job['snapshot_id']))
        source=Path(job['result']['output'])
        with source.open() as f, db:
            h=json.loads(next(f));ended=False
            if (h['sha256'],h['snapshot_id']) != (sha,job['snapshot_id']):raise ValueError('Gap identity mismatch')
            for line in f:
                r=json.loads(line)
                if ended:raise ValueError('Data after completion')
                if r['kind']=='complete':ended=True;continue
                if r['kind']!='unresolved_computed_transfer':continue
                data=pe.read(int(r['address'],16),8) or b''
                trap=trap_kind(r['instruction'],data)
                category='processor_trap' if trap else 'no_high_slice'
                db.execute('INSERT INTO sites(sha256,address,function_address,instruction,instruction_bytes,trap,classification,priority) VALUES(?,?,?,?,?,?,?,?)',
                    (sha,r['address'],r.get('function'),r['instruction'],data.hex(),trap,category,PRIORITY[category]))
            if not ended:raise ValueError('Incomplete gap source')
        db.execute('INSERT INTO sources VALUES(?,?)',(str(source),digest(source)))
    for entry in catalog:
        source=Path(entry['output'])
        with source.open() as f, db:
            h=json.loads(next(f));sha=h['sha256'];ended=False
            identity=db.execute('SELECT snapshot_id FROM programs WHERE sha256=?',(sha,)).fetchone()
            if identity is None or identity[0]!=h['snapshot_id']:raise ValueError('Dispatch identity mismatch')
            for line in f:
                r=json.loads(line)
                if ended:raise ValueError('Dispatch data after completion')
                if r['kind']=='complete':ended=True;continue
                if r['kind']!='transfer':continue
                classification=classify(r);priority=PRIORITY[classification]
                db.execute('INSERT INTO evidence VALUES(?,?,?,?)',(sha,r['site'],str(source),json.dumps(r,separators=(',',':'))))
                db.execute('UPDATE sites SET has_high_slice=1,classification=CASE WHEN priority>? THEN ? ELSE classification END,priority=MIN(priority,?) WHERE sha256=? AND address=?',
                           (priority,classification,priority,sha,r['site']))
            if not ended:raise ValueError('Incomplete dispatch source')
            db.execute('INSERT INTO sources VALUES(?,?)',(str(source),digest(source)))
    db.commit()
    totals=dict(db.execute('SELECT classification,COUNT(*) FROM sites GROUP BY classification'))
    report=dict(sites=db.execute('SELECT COUNT(*) FROM sites').fetchone()[0],classification=totals,
        trap_types=dict(db.execute('SELECT trap,COUNT(*) FROM sites WHERE trap IS NOT NULL GROUP BY trap')),
        unowned_sites=db.execute('SELECT COUNT(*) FROM sites WHERE function_address IS NULL').fetchone()[0],
        unowned_nontrap_sites=db.execute('SELECT COUNT(*) FROM sites WHERE function_address IS NULL AND trap IS NULL').fetchone()[0],
        sqlite_integrity=db.execute('PRAGMA quick_check').fetchone()[0],
        runtime_targets_proven=0,
        interpretation='Classification and static candidates, not runtime target proof. Gap sites are the denominator; several high-pcode transfers can share one site.')
    if report['sqlite_integrity']!='ok':raise ValueError('Index integrity failure')
    db.close();path.replace(root/'dispatch.sqlite')
    return report


def run(args):
    root=args.directory.resolve();root.mkdir(parents=True,exist_ok=False)
    queue=json.loads(args.queue.read_text());jobs=[]
    programs={p['sha256']:p for p in queue['programs']}
    for j in queue['jobs'].values():
        if j['kind']!='indirect':continue
        p=j['program']
        jobs.append(dict(sha256=p['sha256'],snapshot_id=j['snapshot_id'],binary_path=p['binary_path'],
            database=programs[p['sha256']]['database'],source=j['result']['output'],
            output=str(root/(j['id']+'.jsonl'))))
    manifest=next(iter(queue['jobs'].values()))['manifest']
    sage=subprocess.run([manifest['sage_python'],str(HERE/'sage_probe.py'),'--command',manifest['sage_command']],
        check=True,capture_output=True,text=True,timeout=45)
    telemetry=json.loads(sage.stdout)
    if not telemetry.get('metrics') or available()<12:raise RuntimeError('SystemSage/RAM gate failed')
    if shutil.disk_usage(root).free<26*GIB:raise RuntimeError('Disk guard reached')
    workers=min(args.workers,max(1,int((available()-8)/.5)))
    atomic_json(root/'systemsage.json',telemetry);atomic_json(root/'plan.json',jobs)
    catalog=[];counts=Counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(indirect,j):j for j in jobs}
        for f in as_completed(pending):
            j=pending[f];summary=f.result();counts.update(summary)
            catalog.append(dict(output=j['output'],sha256=j['sha256'],snapshot_id=j['snapshot_id'],summary=summary))
            atomic_json(root/'status.json',dict(state='enriching',complete=False,jobs=len(jobs),completed=len(catalog),workers=workers,totals=dict(counts)))
    atomic_json(root/'catalog.json',catalog)
    report=index(queue,catalog,root)
    atomic_json(root/'status.json',dict(state='complete',complete=True,jobs=len(jobs),completed=len(catalog),workers=workers,totals=dict(counts),dispatch=report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('queue',type=Path)
    p.add_argument('directory',type=Path);p.add_argument('--workers',type=int,default=42,choices=range(1,43))
    run(p.parse_args())
