#!/usr/bin/env python3
"""Overlay verified dispatch recoveries without rewriting historical failures or claiming runtime targets."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import shutil
import sqlite3
import struct

from dragonshard import GIB, atomic_json, digest
from dispatch_corpus import classify, PRIORITY
from static_evidence import PE, indirect


def memory_operand(site, raw):
    """The supported FF /2 or /4 RIP-relative forms, including one REX prefix."""
    prefix=1 if raw and 0x40<=raw[0]<=0x4f else 0
    if len(raw)<prefix+6 or raw[prefix]!=0xff or raw[prefix+1] not in (0x15,0x25): return None
    return site+prefix+6+struct.unpack_from('<i',raw,prefix+2)[0]


def run(z, c, root):
    root.mkdir(parents=True,exist_ok=False)
    if shutil.disk_usage(root).free<24*GIB:raise RuntimeError('Disk reserve reached')
    queue=json.loads((z/'swarm-zfs-v10/queue.json').read_text())
    programs={p['sha256']:p for p in queue['programs']}
    inventory=json.loads((c/'dispatch-recovery-v1/inventory.json').read_text())
    required={(r['sha256'],s) for r in inventory['unmatched'] for s in r['sites']}
    pe={sha:PE(p['program']['binary_path'],sha) for sha,p in programs.items()}
    chosen={};instructions={};contexts={};provenance={};extra_functions=[]
    for name in ['dispatch-recovery-v1','dispatch-retained-v1','dispatch-unwind-v2']:
        paths=list((z/name).glob('audit/*/indirect/*.jsonl'))+list((z/name).glob('*/indirect/*.jsonl'))
        for path in paths:
            with path.open() as f:
                header=json.loads(next(f));sha=header['sha256'];ended=False
                for line in f:
                    row=json.loads(line)
                    if row['kind']=='complete':ended=True;continue
                    if ended:raise ValueError('Evidence after completion')
                    if row['kind']!='function':continue
                    if header.get('private_preparation'):
                        extra_functions.append(dict(sha256=sha,snapshot_id=header['snapshot_id'],
                            source=str(path),preparation=header['private_preparation'],function=row))
                    for ins in row.get('instruction_evidence',[]):
                        raw=bytes.fromhex(ins['bytes'])
                        if pe[sha].read(int(ins['site'],16),len(raw))!=raw:raise ValueError('Instruction bytes changed')
                        instructions[sha,ins['site']]=dict(ins,source=str(path))
                    for style,sl in [('ordinary',row.get('slice')),('normalize',row.get('normalization',{}).get('slice'))]:
                        if not sl:continue
                        for t in sl['transfers']:
                            chosen[sha,t['site']]=dict(function=row['address'],slice=sl,transfer=t,
                                source=str(path),snapshot_id=header['snapshot_id'],style=style)
                if not ended:raise ValueError('Incomplete source')
            provenance[str(path)]=digest(path)
    for path in (z/'dispatch-recovery-v1/tasks').glob('*/evidence.json'):
        report=json.loads(path.read_text());request=json.loads((path.parent/'request.json').read_text())
        if not report.get('complete'):raise ValueError('Incomplete exception recovery')
        sha=report['sha256'];row=report['functions'][0]
        for ins in row['instructions']:
            raw=bytes.fromhex(ins['bytes'])
            if pe[sha].read(int(ins['address'],16),len(raw))!=raw:raise ValueError('Recovery bytes changed')
        sl=row['normalization_attempt']['indirect_slice']
        if {t['site'] for t in sl['transfers']}!=set(request['indirect_sites']):raise ValueError('Missing failed-function site')
        for t in sl['transfers']:
            chosen[sha,t['site']]=dict(function=row['address'],slice=sl,transfer=t,
                source=str(path),snapshot_id=report['snapshot_id'],style='reviewed_recovery_normalize')
        provenance[str(path)]=digest(path)
    for path in (z/'dispatch-context-v1/audit').glob('*/indirect/*.jsonl'):
        with path.open() as f:
            header=json.loads(next(f));sha=header['sha256']
            for line in f:
                row=json.loads(line)
                if row['kind']!='site_context':continue
                for ins in row['instructions']:
                    raw=bytes.fromhex(ins['bytes'])
                    if pe[sha].read(int(ins['address'],16),len(raw))!=raw:raise ValueError('Context bytes changed')
                contexts[sha,row['site']]=dict(row,sha256=sha,source=str(path))
        provenance[str(path)]=digest(path)
    if not required<=instructions.keys():raise ValueError('Original unmatched instruction inventory incomplete')
    atomic_json(root/'provenance.json',provenance)
    atomic_json(root/'raw-contexts.json',list(contexts.values()))
    atomic_json(root/'recovered-regions.json',extra_functions)
    grouped=defaultdict(list)
    for (sha,site),r in chosen.items():
        grouped[sha].append(dict(kind='function',address=r['function'],requested_sites=[site],status='ok',
            recovery_source=r['source'],style=r['style'],slice=dict(r['slice'],transfers=[r['transfer']],sites_without_matching_high_pcode=[])))
    catalog=[]
    for sha,rows in grouped.items():
        source=root/(sha[:16]+'.slices.jsonl');snapshot=next(v['snapshot_id'] for (s,_),v in chosen.items() if s==sha)
        with source.open('x') as f:
            for row in [dict(kind='program',sha256=sha,snapshot_id=snapshot),*rows,dict(kind='complete',attempted=len(rows))]:
                f.write(json.dumps(row,separators=(',',':'))+'\n')
        output=root/(sha[:16]+'.targets.jsonl')
        summary=indirect(dict(sha256=sha,snapshot_id=snapshot,binary_path=programs[sha]['program']['binary_path'],
            database=programs[sha]['database'],source=str(source),output=str(output)))
        catalog.append(dict(sha256=sha,output=str(output),summary=summary))
    base=c/'dispatch-enriched-v2/dispatch.sqlite';target=root/'dispatch.sqlite'
    src=sqlite3.connect(base.as_uri()+'?mode=ro',uri=True);db=sqlite3.connect(target);src.backup(db);src.close()
    db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA max_page_count=262144')
    db.execute('ALTER TABLE sites ADD COLUMN recovery_evidence TEXT')
    for entry in catalog:
        with open(entry['output']) as f:
            h=json.loads(next(f));sha=h['sha256']
            for line in f:
                r=json.loads(line)
                if r['kind']!='transfer':continue
                category=classify(r)
                db.execute('INSERT INTO evidence VALUES(?,?,?,?)',(sha,r['site'],entry['output'],json.dumps(r,separators=(',',':'))))
                db.execute('UPDATE sites SET has_high_slice=1,classification=?,priority=?,recovery_evidence=? WHERE sha256=? AND address=? AND trap IS NULL',
                    (category,PRIORITY[category],entry['output'],sha,r['site']))
    manual=[]
    for (sha,site),context in contexts.items():
        current=db.execute('SELECT has_high_slice,classification FROM sites WHERE sha256=? AND address=?',(sha,site)).fetchone()
        if not current or current[0]:continue
        ins=next(i for i in context['instructions'] if i['address']==site);raw=bytes.fromhex(ins['bytes'])
        slot=memory_operand(int(site,16),raw);symbol=pe[sha].imports.get(slot)
        record=dict(sha256=sha,site=site,assembly=ins['assembly'],bytes=ins['bytes'],context_source=context['source'],runtime_target_proven=False)
        category='runtime_register_or_argument'
        if symbol:
            category='named_import';record.update(import_slot=f'{slot:x}',import_symbol=symbol)
        elif site=='1803b72d4' and programs[sha]['name']=='Microsoft.ui.xaml.dll':
            rva,size=pe[sha].directories[10]
            if size<128 or int.from_bytes(pe[sha].read(pe[sha].base+rva+120,8),'little')!=slot:
                raise ValueError('CFG dispatch pointer mismatch')
            category='object_dispatch_slot';record.update(mechanism='CFG dispatcher',dispatch_slot=16,
                pointer=f'{slot:x}',explanation='MOV RAX,[RCX]; MOV RAX,[RAX+0x10] supplies the checked target. Receiver identity remains runtime-dependent.')
        elif site=='1803018f3' and programs[sha]['name']=='d2d1.dll':
            checks={0x1801118fe:'488d15fbe6eeff',0x180111915:'0f85c6ff1e00',
                0x1803018e1:'83fb0b77334863c38b8c82321930004803caffe1'}
            for address,text in checks.items():
                if pe[sha].read(address,len(text)//2)!=bytes.fromhex(text):raise ValueError('Reviewed switch bytes changed')
            targets=[pe[sha].base+x for x in struct.unpack('<12I',pe[sha].read(0x180301932,48))]
            if set(targets)!={0x18011191b}:raise ValueError('Reviewed table changed')
            category='static_candidate';record.update(mechanism='bounded image-relative switch',
                table='180301932',input_range=[0,11],unique_targets=['18011191b'],
                explanation='Predecessor loads RDX=image base; unsigned bound check restricts EBX to 0..11; all 12 immutable table entries select the same target. Cold block is attributed to a different function by the original listing.')
        else:
            m=re.search(r'\[(R\w+)(?: \+ 0x([0-9a-f]+))?\]',ins['assembly'])
            if slot is not None:
                category='runtime_storage';record.update(storage_address=f'{slot:x}',
                    writable=bool((pe[sha].section(slot) or {}).get('writable')),
                    explanation='Indirect transfer through a global memory cell; its runtime value is not established.')
            elif m:
                category='object_dispatch_slot';record.update(base_register=m[1],dispatch_slot=int(m[2] or '0',16),
                    explanation='Instruction-level memory dispatch; neither receiver type nor function boundary is proven.')
        record['classification']=category;manual.append(record)
        db.execute('INSERT INTO evidence VALUES(?,?,?,?)',(sha,site,str(root/'manual-dispatch.json'),json.dumps(record)))
        db.execute('UPDATE sites SET classification=?,priority=?,recovery_evidence=? WHERE sha256=? AND address=?',
            (category,PRIORITY[category],str(root/'manual-dispatch.json'),sha,site))
    atomic_json(root/'manual-dispatch.json',manual);atomic_json(root/'catalog.json',catalog)
    db.commit()
    unresolved=set(required)-set(chosen)
    disposition=Counter()
    for key in required:
        ins=instructions[key]
        if ins['assembly'].startswith(('INT3','INT ')):disposition['processor_traps']+=1
        elif key in chosen:disposition['recovered_high_transfer']+=1
        else:disposition['instruction_level_resolution']+=1
    failure_sites={(r['sha256'],s) for r in inventory['failed'] for s in r['requested_sites']}
    if not failure_sites<=chosen.keys():raise ValueError('Incomplete failed-function recovery coverage')
    report=dict(complete=True,original_unmatched_sites=len(required),disposition=dict(disposition),
        failed_functions=len(inventory['failed']),failed_function_sites=len(failure_sites),
        failed_sites_with_recovered_slices=len(failure_sites & chosen.keys()),
        verified_instruction_contexts=len(contexts),
        classification=dict(db.execute('SELECT classification,COUNT(*) FROM sites GROUP BY classification')),
        orphan_sites_with_recovered_slices=db.execute('SELECT COUNT(*) FROM sites WHERE function_address IS NULL AND trap IS NULL AND has_high_slice=1').fetchone()[0],
        nontrap_sites_without_high_slices=db.execute('SELECT COUNT(*) FROM sites WHERE trap IS NULL AND has_high_slice=0').fetchone()[0],
        total_gap_sites=db.execute('SELECT COUNT(*) FROM sites').fetchone()[0],
        runtime_targets_proven=0,
        limit='All sites have categorized evidence, not all runtime destinations. Import names and bounded static targets differ from receiver/callback identities. High-pcode omissions remain explicit.',
        sqlite_integrity=db.execute('PRAGMA quick_check').fetchone()[0])
    if report['classification'].get('no_high_slice',0) or report['sqlite_integrity']!='ok':raise ValueError('Unaccounted sites or index damage')
    atomic_json(root/'status.json',report);db.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('zfs','captures','directory'):p.add_argument(name,type=Path)
    a=p.parse_args();run(a.zfs.resolve(),a.captures.resolve(),a.directory.resolve())
