#!/usr/bin/env python3
"""Incrementally index completed enrichment evidence; retain all ambiguous/unresolved outcomes."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import sqlite3
import time

from dragonshard import GIB, atomic_json, digest

SCHEMA='''
CREATE TABLE sources(path TEXT PRIMARY KEY,sha256 TEXT,records INTEGER);
CREATE TABLE evidence(id INTEGER PRIMARY KEY, binary_sha256 TEXT,kind TEXT,address TEXT,owner TEXT,payload TEXT);
CREATE INDEX evidence_address ON evidence(binary_sha256,address);
CREATE INDEX evidence_kind ON evidence(kind,binary_sha256);
CREATE TABLE class_members(binary_sha256 TEXT,table_address TEXT,slot INTEGER,function_address TEXT,type_name TEXT);
CREATE INDEX class_function ON class_members(binary_sha256,function_address);
CREATE INDEX class_table ON class_members(binary_sha256,table_address,slot);
CREATE TABLE library_candidates(binary_sha256 TEXT,address TEXT,library TEXT,name TEXT,method TEXT,payload TEXT);
CREATE INDEX library_function ON library_candidates(binary_sha256,address);
CREATE TABLE fingerprints(binary_sha256 TEXT,address TEXT,status TEXT,full_hash TEXT,specific_hash TEXT,payload TEXT,
 PRIMARY KEY(binary_sha256,address));
CREATE INDEX fid_hash ON fingerprints(full_hash,specific_hash);
CREATE VIRTUAL TABLE search USING fts5(binary_sha256 UNINDEXED,address UNINDEXED,text);
'''


def ingest(db,path,sha=None,exception=False):
    identity=digest(path)
    previous=db.execute('SELECT sha256 FROM sources WHERE path=?',(str(path),)).fetchone()
    if previous:
        if previous[0]!=identity:raise ValueError('Previously indexed evidence changed')
        return
    counts=0
    if exception:
        obj=json.loads(path.read_text())
        if not obj.get('complete'):raise ValueError('Incomplete exception report')
        if sha and obj['sha256'] != sha:raise ValueError('Binary identity mismatch')
        sha=obj['sha256'];rows=[dict(r,kind='decompiler_exception',retry_settings={k:obj.get(k) for k in
            ('snapshot_id','timeout_seconds','max_instructions','payload_mib')}) for r in obj['functions']]
    else:
        rows=(json.loads(line) for line in path.open())
    with db:
        ended=exception
        for row in rows:
            kind=row['kind']
            if kind=='program':
                if sha and row['sha256']!=sha:raise ValueError('Binary identity mismatch')
                sha=row['sha256'];continue
            if kind=='complete':ended=True;continue
            if not exception and ended:raise ValueError('Data after completion marker')
            if not sha:raise ValueError('Missing binary identity')
            address=row.get('address',row.get('site',row.get('start')))
            payload=json.dumps(row,separators=(',',':'))
            if kind=='function':
                db.execute('INSERT INTO fingerprints VALUES(?,?,?,?,?,?)',
                    (sha,address,row['status'],row.get('full_hash'),row.get('specific_hash'),payload))
                for match in row.get('matches',[]):
                    db.execute('INSERT INTO library_candidates VALUES(?,?,?,?,?,?)',
                        (sha,address,match['library'],match['name'],'fid_candidate',json.dumps(match)))
            else:
                db.execute('INSERT INTO evidence VALUES(NULL,?,?,?,?,?)',(sha,kind,address,row.get('function'),payload))
                if kind=='function_pointer_table_candidate':
                    typename=(row.get('rtti') or {}).get('name')
                    for member in row['entries']:
                        db.execute('INSERT INTO class_members VALUES(?,?,?,?,?)',
                            (sha,address,member['slot'],member['target'],typename))
                    if typename:db.execute('INSERT INTO search VALUES(?,?,?)',(sha,address,typename))
                elif kind=='string_evidence':
                    db.execute('INSERT INTO search VALUES(?,?,?)',(sha,address,row['value']))
                    for library in row['libraries']:
                        db.execute('INSERT INTO library_candidates VALUES(?,?,?,?,?,?)',
                            (sha,address,library,row['value'],'literal_presence',payload))
                elif kind=='function_library_evidence':
                    db.execute('INSERT INTO search VALUES(?,?,?)',(sha,address,payload))
            counts+=1
        if not ended:raise ValueError('Missing completion marker')
        if identity!=digest(path):raise ValueError('Evidence changed while indexing')
        db.execute('INSERT INTO sources VALUES(?,?,?)',(str(path),identity,counts))


def run(args):
    root=args.directory.resolve();root.mkdir(parents=True,exist_ok=False)
    database=root/'evidence.sqlite';db=sqlite3.connect(database)
    db.execute('PRAGMA max_page_count=524288') # 2 GiB at the checked 4 KiB page size.
    if db.execute('PRAGMA page_size').fetchone()[0]!=4096:raise ValueError('Unexpected SQLite page size')
    db.execute('PRAGMA temp_store=MEMORY');db.executescript(SCHEMA)
    status=dict(state='indexing',complete=False,database=str(database),sources=0,full_analysis_complete=False)
    atomic_json(root/'status.json',status)
    try:
        static=json.loads((args.static/'status.json').read_text())
        if not static['complete']:raise ValueError('Static evidence pass is incomplete')
        for item in json.loads((args.static/'catalog.json').read_text()):
            if shutil.disk_usage(root).free<22*GIB:raise RuntimeError('Disk reserve reached')
            ingest(db,Path(item['output']),item['sha256'])
            status['sources']+=1;atomic_json(root/'status.json',status)
        status['state']='following_enrichment'
        while True:
            queue=json.loads((args.enrichment/'queue.json').read_text())
            for job in queue:
                if job['state']!='complete':continue
                path=args.enrichment/'tasks'/job['id']/('evidence.json' if job['kind']=='exception' else 'evidence.jsonl')
                if db.execute('SELECT 1 FROM sources WHERE path=?',(str(path),)).fetchone():continue
                if shutil.disk_usage(root).free<22*GIB:raise RuntimeError('Disk reserve reached')
                ingest(db,path,job['program']['sha256'],job['kind']=='exception')
            status.update(sources=db.execute('SELECT COUNT(*) FROM sources').fetchone()[0],
                fingerprints=db.execute('SELECT COUNT(*) FROM fingerprints').fetchone()[0],
                evidence_records=db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0])
            atomic_json(root/'status.json',status)
            state=json.loads((args.enrichment/'status.json').read_text())
            if state['state'] in ('complete','blocked_or_failed'):
                # queue.json is published after status.json; reread once before final validation.
                queue=json.loads((args.enrichment/'queue.json').read_text())
                completed=sum(j['state']=='complete' for j in queue)
                indexed=db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]-len(json.loads((args.static/'catalog.json').read_text()))
                if indexed!=completed:time.sleep(2);continue
                status.update(state=state['state'],complete=state['complete']);break
            time.sleep(5)
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok':raise RuntimeError('Index integrity failed: '+integrity)
        status['sqlite_integrity']=integrity;atomic_json(root/'status.json',status)
    except BaseException as error:
        status.update(state='blocked_or_failed',complete=False,error=str(error));atomic_json(root/'status.json',status);raise
    finally:db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('static','enrichment','directory'):parser.add_argument(name,type=Path)
    run(parser.parse_args())
