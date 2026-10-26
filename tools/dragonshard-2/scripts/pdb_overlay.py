#!/usr/bin/env python3
"""Index exact GUID/DBI-age matched public PDB names without rewriting analysis snapshots."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import subprocess
import urllib.error
import urllib.request
import uuid

from dragonshard import GIB, atomic_json, digest
from static_evidence import PE, Output


def codeview(pe):
    if len(pe.directories)<=6:return None
    rva,size=pe.directories[6]
    for offset in range(0,size,28):
        raw=pe.read(pe.base+rva+offset,28)
        if raw is None:break
        _,_,_,_,kind,length,addr,file_offset=struct.unpack('<IIHHIIII',raw)
        raw=pe.data[file_offset:file_offset+length]
        if kind!=2 or raw[:4]!=b'RSDS':continue
        name=raw[24:].split(b'\0')[0].decode('utf-8','replace').replace('\\','/').split('/')[-1]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):raise ValueError('Unsafe PDB filename')
        guid=uuid.UUID(bytes_le=raw[4:20]).hex.upper();age=struct.unpack_from('<I',raw,20)[0]
        return dict(name=name,guid=guid,age=age,key=guid+f'{age:X}')


def identifiers(path):
    result=subprocess.run(['llvm-pdbutil','pdb2yaml','--pdb-stream','--dbi-stream','--no-file-headers',str(path)],
                          capture_output=True,check=True)
    # PDB names and producer metadata are byte strings.  Decode with replacement
    # at this boundary so one legacy high-bit name cannot discard an otherwise
    # identity-matched public symbol file.
    text=result.stdout.decode('utf-8','replace')
    pdb=text.split('PdbStream:',1)[1].split('DbiStream:',1)[0]
    dbi=text.split('DbiStream:',1)[1] if 'DbiStream:' in text else ''
    age=lambda s:int(re.search(r'Age:\s*(\d+)',s).group(1))
    # Ghidra AbstractPdb.getIdentifiers uses DBI age when available; stripped PDB
    # stream age can differ. Preserve both rather than rejecting correct public PDBs.
    return dict(guid=re.search(r'Guid:\s*.*?\{([0-9A-Fa-f-]+)\}',pdb).group(1).replace('-','').upper(),
                age=age(dbi) if re.search(r'Age:\s*[1-9]',dbi) else age(pdb),pdb_stream_age=age(pdb))


def publics(text,pe):
    pending=None
    for line in text.splitlines():
        m=re.search(r'S_PUB32 .*?`(.*)`\s*$',line)
        if m:pending=m.group(1);continue
        m=re.search(r'flags = (.*?), addr = (\d+):(\d+)',line)
        if m and pending is not None:
            section,offset=int(m[2]),int(m[3]);name=pending;pending=None
            if 1<=section<=len(pe.sections):
                s=pe.sections[section-1]
                if offset<max(s['size'],s['virtual_size']):
                    yield dict(address=f'{s["start"]+offset:x}',name=name,section=section,
                               offset=offset,function='function' in m[1])


def demangle(names):
    selected=sorted({n for n in names if n.startswith('?') and n.isascii()})
    result={}
    for start in range(0,len(selected),1000):
        batch=selected[start:start+1000]
        p=subprocess.run(['llvm-undname'],input='\n'.join(batch)+'\n',capture_output=True,text=True)
        # A single unsupported mangling returns 1 while other names are decoded.
        # Preserve its exact public symbol, rather than discarding an entire PDB.
        if p.returncode not in (0,1):raise RuntimeError('llvm-undname failed: '+p.stderr)
        for group in p.stdout.strip().split('\n\n'):
            lines=group.splitlines()
            if len(lines)>=2 and lines[0] in batch:result[lines[0]]=' '.join(lines[1:])
    return result


def verify_section_layout(headers, pe):
    if re.search(r'\[.*OMAP', headers, re.I):
        raise ValueError('OMAP transform needs explicit support')
    sections = {}
    # llvm-pdbutil can print bytes beyond an eight-byte COFF section name.
    # Parse numbered blocks and numeric fields, never the displayed name.
    for match in re.finditer(r'SECTION HEADER #(\d+)(.*?)(?=SECTION HEADER #|\Z)', headers, re.S):
        number = int(match[1])
        address = re.search(r'^\s*([0-9A-Fa-f]+) virtual address\s*$', match[2], re.M)
        if number in sections or address is None:
            raise ValueError('Duplicate or malformed PDB section header')
        sections[number] = int(address[1], 16)
    if set(sections) != set(range(1, len(pe.sections) + 1)):
        raise ValueError('PDB section-header coverage mismatch')
    for number, rva in sections.items():
        if rva != pe.sections[number - 1]['start'] - pe.base:
            raise ValueError('PDB section layout mismatch')


def index_destination(root, resume):
    base = root / ('symbols-recovered.sqlite' if resume else 'symbols.sqlite')
    if not resume:
        if base.exists() or base.with_suffix('.sqlite.partial').exists():
            raise ValueError('Refusing to overwrite an existing symbol index')
        return base
    candidate = base
    serial = 2
    while candidate.exists() or candidate.with_suffix('.sqlite.partial').exists():
        candidate = root / f'symbols-recovered-{serial}.sqlite'
        serial += 1
    return candidate


def process(spec,root,local,download):
    pe=PE(spec['binary_path'],spec['sha256']);cv=codeview(pe)
    if cv is None:return dict(sha256=spec['sha256'],state='no_codeview')
    expected={k:cv[k] for k in ('guid','age')};selected=None;metadata=None;mismatched=[]
    for path in local.get(cv['name'].lower(),[]):
        try:meta=identifiers(path)
        except (ValueError,subprocess.CalledProcessError,AttributeError):continue
        if all(meta[k]==v for k,v in expected.items()):selected=path;metadata=meta;break
        mismatched.append(str(path))
    url='https://msdl.microsoft.com/download/symbols/'+cv['name']+'/'+cv['key']+'/'+cv['name']
    if selected is None and download:
        destination=root/'symbols'/cv['name']/cv['key']/cv['name'];destination.parent.mkdir(parents=True)
        try:
            with urllib.request.urlopen(url,timeout=60) as response, destination.with_suffix('.partial').open('xb') as stream:
                size=0
                while block:=response.read(1024**2):
                    size+=len(block)
                    if size>256*1024**2 or shutil.disk_usage(root).free<22*GIB:raise RuntimeError('PDB download budget reached')
                    stream.write(block)
            destination.with_suffix('.partial').replace(destination)
            metadata=identifiers(destination)
            if not all(metadata[k]==v for k,v in expected.items()):raise ValueError('Downloaded PDB identity mismatch')
            selected=destination
        except urllib.error.HTTPError as error:
            if error.code!=404:raise
            return dict(sha256=spec['sha256'],state='not_published_at_checked_url',codeview=cv,url=url,mismatched_local=mismatched)
    if selected is None:return dict(sha256=spec['sha256'],state='no_matching_local_pdb',codeview=cv)
    output=root/(spec['sha256'][:16]+'.pdb.jsonl')
    before=digest(selected)
    headers=subprocess.run(['llvm-pdbutil','dump','--section-headers','--streams',str(selected)],capture_output=True,check=True).stdout.decode('utf-8','replace')
    verify_section_layout(headers, pe)
    raw=root/(spec['sha256'][:16]+'.publics.txt')
    if not raw.exists():
        with raw.with_suffix('.partial').open('x') as stream:
            subprocess.run(['llvm-pdbutil','dump','--publics',str(selected)],stdout=stream,check=True)
        raw.with_suffix('.partial').replace(raw)
    # Raw records preserve producer bytes; the searchable Unicode view explicitly
    # marks names that needed decoding replacement.
    records=list(publics(raw.read_text(errors='replace'),pe));names=demangle(r['name'] for r in records)
    db=sqlite3.connect(Path(spec['database']).resolve().as_uri()+'?mode=ro',uri=True)
    functions={a:n for a,n in db.execute('SELECT address,name FROM functions WHERE sha256=?',(spec['sha256'],))};db.close()
    mapped=set();anonymous=set();out=Output(output)
    out.emit(dict(kind='program',sha256=spec['sha256'],snapshot_id=spec['snapshot_id'],pdb=str(selected),pdb_sha256=before,
                  identifiers=metadata,codeview=cv,section_layout_verified=True,raw_publics=str(raw)))
    for r in records:
        if r['address'] in functions:
            mapped.add(r['address'])
            if functions[r['address']].startswith('FUN_'):anonymous.add(r['address'])
        out.emit(dict(r,kind='public_symbol',demangled=names.get(r['name']),
                      name_decoding_replacement='\ufffd' in r['name'],
                      demangle_status='decoded' if r['name'] in names else 'unsupported' if r['name'].startswith('?') else 'not_mangled',
                      recognized_function=functions.get(r['address'])))
    if before!=digest(selected):raise ValueError('PDB changed during extraction')
    summary=dict(symbols=len(records),recognized_function_addresses_named=len(mapped),anonymous_functions_named=len(anonymous),
                 unsupported_mangled_names=sum(r['name'].startswith('?') and r['name'] not in names for r in records))
    out.finish(summary)
    return dict(sha256=spec['sha256'],state='complete',output=str(output),pdb=str(selected),**summary)


def run(args):
    root=args.directory.resolve();root.mkdir(parents=True,exist_ok=args.resume)
    local={}
    for path in args.local.rglob('*.pdb'):local.setdefault(path.name.lower(),[]).append(path)
    specs=[s for s in json.loads(args.plan.read_text()) if s['kind']=='binary']
    results=[]
    if args.resume:
        previous=json.loads((root/'status.json').read_text())
        if previous['state']=='running':raise ValueError('Cannot resume a running symbol export')
        results=[r for r in json.loads((root/'catalog.json').read_text()) if r['state']!='failed']
        for r in results:
            if r['state']=='complete':
                with Path(r['output']).open() as stream:
                    header=json.loads(next(stream));last=None
                    for line in stream:last=json.loads(line)
                if not last or last['kind']!='complete' or digest(Path(header['pdb']))!=header['pdb_sha256']:
                    raise ValueError('Saved PDB output identity/completion mismatch')
    status=dict(state='running',complete=False,programs=len(specs),checked=len(results))
    atomic_json(root/'status.json',status)
    with ThreadPoolExecutor(max_workers=8) as pool:
        done={r['sha256'] for r in results}
        pending={pool.submit(process,s,root,local,args.download):s for s in specs if s['sha256'] not in done}
        for future in as_completed(pending):
            spec=pending[future]
            try:results.append(future.result())
            except Exception as error:results.append(dict(sha256=spec['sha256'],state='failed',error=str(error)))
            status['checked']=len(results);atomic_json(root/'catalog.json',results);atomic_json(root/'status.json',status)
    database=index_destination(root, args.resume)
    partial=database.with_suffix('.sqlite.partial')
    if shutil.disk_usage(root).free < 22*GIB:
        raise RuntimeError('Symbol index disk reserve reached')
    partial.touch(exist_ok=False)
    db=sqlite3.connect(partial)
    if db.execute('PRAGMA page_size').fetchone()[0] != 4096:
        raise ValueError('Unexpected SQLite page size')
    db.execute('PRAGMA max_page_count=524288')
    db.execute('PRAGMA temp_store=MEMORY')
    db.executescript('''
CREATE TABLE symbols(binary_sha256 TEXT,address TEXT,name TEXT,demangled TEXT,recognized_function TEXT);
CREATE INDEX symbol_address ON symbols(binary_sha256,address);
CREATE VIRTUAL TABLE search USING fts5(binary_sha256 UNINDEXED,address UNINDEXED,name,demangled);
''')
    with db:
        for result in results:
            if result['state']!='complete':continue
            if shutil.disk_usage(root).free < 22*GIB:
                raise RuntimeError('Symbol index disk reserve reached')
            for line in Path(result['output']).open():
                row=json.loads(line)
                if row['kind']!='public_symbol':continue
                values=(result['sha256'],row['address'],row['name'],row.get('demangled'))
                db.execute('INSERT INTO symbols VALUES(?,?,?,?,?)',(*values,row.get('recognized_function')))
                db.execute('INSERT INTO search VALUES(?,?,?,?)',values)
    status.update(state='complete' if all(r['state']!='failed' for r in results) else 'blocked_or_failed',
                  complete=all(r['state']!='failed' for r in results),
                  matching_pdbs=sum(r['state']=='complete' for r in results),
                  public_symbols=sum(r.get('symbols',0) for r in results),
                  anonymous_functions_named=sum(r.get('anonymous_functions_named',0) for r in results),
                  database=str(database))
    status['sqlite_integrity']=db.execute('PRAGMA integrity_check').fetchone()[0];db.close()
    if status['sqlite_integrity'] != 'ok':
        raise ValueError('Symbol index integrity check failed; partial retained')
    partial.replace(database)
    atomic_json(root/'status.json',status)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for n in ('plan','local','directory'):parser.add_argument(n,type=Path)
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--resume',action='store_true')
    run(parser.parse_args())
