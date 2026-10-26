#!/usr/bin/env python3
"""Classify PE/library, discovery-gap and saved SSA evidence without inventing runtime identities."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import itertools
import json
import mmap
from pathlib import Path
import re
import shutil
import sqlite3
import struct

from dragonshard import GIB, atomic_json


class PE:
    def __init__(self, path, sha=None):
        self.file = open(path, 'rb')
        self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        if sha and hashlib.sha256(self.data).hexdigest() != sha:
            raise ValueError('Binary hash changed: ' + str(path))
        p = struct.unpack_from('<I', self.data, 0x3c)[0]
        if self.data[:2] != b'MZ' or self.data[p:p+4] != b'PE\0\0':
            raise ValueError('Not PE')
        count, optsize = struct.unpack_from('<H', self.data, p+6)[0], struct.unpack_from('<H', self.data, p+20)[0]
        opt = p+24
        if struct.unpack_from('<H', self.data, opt)[0] != 0x20b:
            raise ValueError('Only the selected PE32+ corpus is supported')
        self.base = struct.unpack_from('<Q', self.data, opt+24)[0]
        self.sections = []
        for i in range(count):
            h = opt + optsize + 40*i
            name, vs, rva, size, offset, _, _, _, _, flags = struct.unpack_from('<8sIIIIIIHHI', self.data, h)
            if offset + size > len(self.data):
                raise ValueError('Section outside file')
            self.sections.append(dict(name=name.rstrip(b'\0').decode('ascii', 'replace'), start=self.base+rva,
                virtual_size=vs, size=size, offset=offset, executable=bool(flags & 0x20000000),
                writable=bool(flags & 0x80000000)))
        ndir = min(struct.unpack_from('<I', self.data, opt+108)[0], 16, max(0, (optsize-112)//8))
        self.directories = [struct.unpack_from('<II', self.data, opt+112+8*i) for i in range(ndir)]
        self.imports = {}
        if len(self.directories) > 1:
            rva, size = self.directories[1]
            for i in range(size//20):
                raw = self.read(self.base+rva+20*i, 20)
                if raw is None: break
                original, _, _, name, iat = struct.unpack('<IIIII', raw)
                if not any((original, name, iat)): break
                dll = self.string(self.base+name)
                for j in range(100000):
                    item = self.read(self.base+(original or iat)+8*j, 8)
                    if item is None: break
                    ptr = int.from_bytes(item, 'little')
                    if not ptr: break
                    symbol = '#' + str(ptr & 0xffff) if ptr >> 63 else self.string(self.base+ptr+2)
                    self.imports[self.base+iat+8*j] = dll + '!' + symbol

    def section(self, address, size=1):
        return next((s for s in self.sections if s['start'] <= address and
                     address+size <= s['start']+s['size']), None)

    def read(self, address, size):
        s = self.section(address, size)
        if s is None: return None
        offset = s['offset'] + address-s['start']
        return self.data[offset:offset+size]

    def string(self, address, maximum=4096):
        s = self.section(address)
        if s is None: return ''
        raw = self.read(address, min(maximum, s['start']+s['size']-address))
        return raw.split(b'\0', 1)[0].decode('utf-8', 'replace')

    def unwind_entries(self):
        if len(self.directories) <= 3: return
        rva, size = self.directories[3]
        for i in range(size//12):
            data = self.read(self.base+rva+i*12, 12)
            if data is None: break
            start, end, info = struct.unpack('<III', data)
            if start and end > start:
                yield self.base+start, self.base+end, self.base+info


class Slice:
    """Conservative finite value candidates; file-memory loads never prove a runtime target."""
    def __init__(self, nodes, pe, limit=64):
        self.nodes = {n['id']: n for n in nodes}; self.pe = pe; self.limit = limit
        self.memo = {}; self.visiting = set(); self.loads = {}; self.unknown = set(); self.truncated = False

    def memory_value(self, address, size):
        """Record global storage as well as explicit LOADs; an IAT cell is not its disk value."""
        data = self.pe.read(address, size)
        self.loads[address] = dict(address=f'{address:x}', size=size,
            import_symbol=self.pe.imports.get(address),
            writable=bool((self.pe.section(address) or {}).get('writable')))
        if data is None:
            self.unknown.add('unmapped_memory'); return set()
        if address in self.pe.imports:
            self.unknown.add('runtime_import_binding'); return set()
        if self.loads[address]['writable']:
            self.unknown.add('runtime_writable_storage')
        return {int.from_bytes(data, 'little')}

    def values(self, i, depth=0):
        if i in self.memo: return self.memo[i]
        if depth > 160 or i in self.visiting or i not in self.nodes:
            self.unknown.add('cycle_or_depth_or_missing_node'); return set()
        self.visiting.add(i); n = self.nodes[i]; op = n.get('opcode'); inputs = n.get('inputs', [])
        mask = (1 << (8*n['size']))-1; result = set()
        v = lambda j: self.values(inputs[j], depth+1)
        if n.get('constant'):
            result = {int(n['offset'], 16)}
        elif not op and n.get('space') == 'ram':
            result = self.memory_value(int(n['offset'], 16), n['size'])
        elif op in ('COPY', 'CAST', 'INT_ZEXT', 'INDIRECT'):
            result = v(0)
            if op == 'INDIRECT': self.unknown.add('indirect_side_effect')
        elif op == 'INT_SEXT':
            bits = self.nodes[inputs[0]]['size']*8
            result = {x-(1 << bits) if x & (1 << (bits-1)) else x for x in v(0)}
        elif op == 'MULTIEQUAL':
            for j in range(len(inputs)): result |= v(j)
        elif op in ('INT_ADD', 'INT_SUB', 'INT_MULT', 'INT_AND', 'INT_OR', 'INT_XOR', 'INT_LEFT', 'INT_RIGHT', 'INT_SRIGHT', 'PTRSUB', 'SUBPIECE', 'PIECE'):
            for a, b in itertools.product(v(0), v(1)):
                if op in ('INT_ADD', 'PTRSUB'): value = a+b
                elif op == 'INT_SUB': value = a-b
                elif op == 'INT_MULT': value = a*b
                elif op == 'INT_AND': value = a&b
                elif op == 'INT_OR': value = a|b
                elif op == 'INT_XOR': value = a^b
                elif op == 'INT_LEFT': value = a << b if b < 8*n['size'] else 0
                elif op == 'INT_RIGHT': value = a >> b
                elif op == 'INT_SRIGHT':
                    bits = self.nodes[inputs[0]]['size']*8
                    value = (a-(1 << bits) if a & (1 << (bits-1)) else a) >> min(b, bits)
                elif op == 'SUBPIECE': value = a >> (8*b)
                else: value = (a << (8*self.nodes[inputs[1]]['size'])) | b
                result.add(value & mask)
                if len(result) > self.limit: break
        elif op == 'PTRADD':
            for a, b, c in itertools.product(v(0), v(1), v(2)):
                result.add((a+b*c) & mask)
                if len(result) > self.limit: break
        elif op == 'LOAD':
            for address in v(1):
                result |= self.memory_value(address, n['size'])
        else:
            self.unknown.add(op or ('input' if n.get('input') else 'unmodeled_storage'))
        if len(result) > self.limit:
            self.truncated = True; result = set(sorted(result)[:self.limit])
        result = {x & mask for x in result}
        self.visiting.remove(i); self.memo[i] = result
        return result

    def slot(self, i):
        seen = set()
        def strip(k):
            while k not in seen:
                seen.add(k); n = self.nodes.get(k, {})
                if n.get('opcode') in ('CAST', 'COPY') and n.get('inputs'): k = n['inputs'][0]
                else: return n
            return {}
        n = strip(i)
        if n.get('opcode') != 'LOAD': return None
        ptr = strip(n['inputs'][1]); op = ptr.get('opcode'); ins = ptr.get('inputs', [])
        if op in ('INT_ADD', 'PTRSUB') and len(ins) == 2:
            for base, offset in (ins, ins[::-1]):
                c = self.nodes[offset]
                if c.get('constant'):
                    return dict(offset=int(c['offset'], 16), base_node=base,
                                interpretation='memory_dispatch_slot_candidate')
        if op == 'LOAD':
            return dict(offset=0, base_node=ptr['id'], interpretation='memory_dispatch_slot_candidate')
        return None

    def dependencies(self, root):
        """Describe the complete saved closure's external dependencies without guessing receiver types."""
        seen, pending, origins, operations = set(), [root], {}, Counter()
        while pending:
            i = pending.pop()
            if i in seen: continue
            seen.add(i); n = self.nodes.get(i)
            if n is None: continue
            op = n.get('opcode')
            if op: operations[op] += 1
            if n.get('input') or (not op and not n.get('constant')) or op in ('CALL', 'CALLIND', 'CALLOTHER'):
                item = {k:n[k] for k in ('space','offset','size','definition_site','opcode','input') if k in n}
                origins[json.dumps(item,sort_keys=True)] = item
            pending.extend(n.get('inputs', []))
        return dict(nodes=len(seen), origins=list(origins.values()), operations=dict(operations))


LIBRARIES = {
    'protobuf': r'google::protobuf|google/protobuf|protobuf::|Protocol Buffers',
    'grpc': r'grpc::|grpc_core::|grpc/|gRPC',
    'boost': r'boost::|boost[/\\]',
    'openssl': r'OpenSSL [0-9]|openssl[/\\]|OPENSSL_[A-Z_]+|SSL_CTX_',
    'boringssl': r'BoringSSL|boringssl[/\\]',
    'sqlite': r'SQLite version|sqlite3_[a-z_]+|sqlite[/\\]',
    'curl': r'libcurl/[0-9]|curl_easy_|curl_multi_',
    'zlib': r'zlib version|zlib [0-9]|deflate [0-9]|inflate [0-9]',
    'chromium_cef': r'Chromium|chrome[/\\]|cef_[a-z_]+|Cef[A-Z]',
    'webrtc': r'webrtc::|webrtc[/\\]',
    'qt': r'QtCore|QtNetwork|Qt5[A-Z]|Qt6[A-Z]',
    'abseil': r'absl::|absl[/\\]',
    'nlohmann_json': r'nlohmann::|nlohmann[/\\]',
    'spdlog': r'spdlog::|spdlog[/\\]',
    'fmt': r'fmt::v[0-9]|fmt[/\\]format',
    'hidapi': r'hidapi|hid_enumerate|hid_open_path',
    'lua': r'Lua [0-9]|luaL_[a-z]+|lua_[a-z]+',
    'logitech': r'Logitech|logitech|lghub|LGHUB|G.Shift',
}
PATTERNS = {k: re.compile(v) for k, v in LIBRARIES.items()}


class Output:
    def __init__(self, path, cap=512*1024**2):
        self.path=Path(path); self.partial=self.path.with_suffix(self.path.suffix+'.partial')
        self.stream=self.partial.open('x');self.bytes=0;self.cap=cap
    def emit(self, row):
        text=json.dumps(row, separators=(',', ':'))+'\n';size=len(text.encode())
        if self.bytes+size>self.cap or shutil.disk_usage(self.path.parent).free<22*GIB:
            raise RuntimeError('Output budget or disk reserve exhausted')
        self.stream.write(text);self.bytes+=size
    def finish(self, summary):
        self.emit(dict(kind='complete', **summary));self.stream.close();self.partial.replace(self.path)


def function_names(spec):
    with sqlite3.connect(Path(spec['database']).resolve().as_uri()+'?mode=ro', uri=True) as db:
        return {int(a, 16): n for a,n in db.execute('SELECT address,name FROM functions WHERE sha256=?', (spec['sha256'],))}


def indirect(spec):
    pe=PE(spec['binary_path'],spec['sha256']);names=function_names(spec);counts=Counter()
    out=Output(spec['output'])
    with open(spec['source']) as stream:
        header=json.loads(next(stream))
        if header['sha256']!=spec['sha256'] or header['snapshot_id']!=spec['snapshot_id']:
            raise ValueError('SSA source identity mismatch')
        out.emit(dict(kind='program', **{k:spec[k] for k in ('sha256','snapshot_id','source')}, runtime_target_proof=False))
        ended=False
        for line in stream:
            r=json.loads(line)
            if ended: raise ValueError('Data after completion')
            if r['kind']=='complete': ended=True;continue
            if r['kind']!='function': continue
            counts['functions']+=1;counts['requested_sites']+=len(r['requested_sites'])
            if r['status']!='ok':
                counts['failed_slice_functions']+=1;out.emit({**r, 'kind':'slice_failure'});continue
            sl=r['slice']
            counts['sites_without_matching_high_pcode']+=len(sl['sites_without_matching_high_pcode'])
            if sl['sites_without_matching_high_pcode']:
                out.emit(dict(kind='unmatched_sites', function=r['address'], sites=sl['sites_without_matching_high_pcode']))
            for t in sl['transfers']:
                counts['transfers']+=1;s=Slice(sl['nodes'],pe);node=s.nodes[t['target_node']]
                direct=t.get('direct_in_decompiler',False)
                vals={int(node['offset'],16)} if direct and node.get('space')=='ram' else s.values(t['target_node'])
                candidates=[]
                for target in sorted(vals):
                    section=pe.section(target)
                    if target in names or (section and section['executable']):
                        candidates.append(dict(address=f'{target:x}', function=names.get(target),
                            evidence='decompiler_direct' if direct else 'static_value_candidate'))
                imports=sorted({v['import_symbol'] for v in s.loads.values() if v['import_symbol']})
                counts['transfers_with_code_candidates']+=bool(candidates)
                counts['transfers_with_import_slots']+=bool(imports)
                slot=s.slot(t['target_node']);counts['dispatch_slot_candidates']+=slot is not None
                if not candidates and not imports:counts['unresolved_transfers']+=1
                out.emit(dict(kind='transfer', function=r['address'], site=t['site'], opcode=t['opcode'],
                    candidates=candidates, import_slots=imports, image_memory_reads=list(s.loads.values()),
                    unknown_inputs=sorted(s.unknown), candidate_set_truncated=s.truncated,
                    dispatch_slot=slot, dependencies=s.dependencies(t['target_node']), runtime_target_proof=False))
        if not ended: raise ValueError('Incomplete SSA input')
    out.finish(dict(counts));return dict(counts)


def binary(spec):
    pe=PE(spec['binary_path'],spec['sha256']);names=function_names(spec);counts=Counter();out=Output(spec['output'])
    out.emit(dict(kind='program',sha256=spec['sha256'],snapshot_id=spec['snapshot_id'],
                  binary_path=spec['binary_path'],sections=pe.sections))
    for s in pe.sections:
        if s['executable']:continue
        blob=pe.data[s['offset']:s['offset']+s['size']]
        # Retain every matching string, including RTTI/build paths. Presence is not function ownership.
        for encoding, pattern in [('ascii', rb'[\x20-\x7e]{6,}'),('utf-16le',rb'(?:[\x20-\x7e]\x00){6,}')]:
            for match in re.finditer(pattern,blob):
                text=match.group().decode(encoding);families=[k for k,p in PATTERNS.items() if p.search(text)]
                if families or text.startswith(('.?AV','.?AU')):
                    counts['library_or_rtti_strings']+=1
                    for family in families:counts['string_family:'+family]+=1
                    out.emit(dict(kind='string_evidence',address=f'{s["start"]+match.start():x}',
                        encoding=encoding,value=text,libraries=families,proof='literal_presence_only'))
        if s['writable']:continue
        run=[];run_start=0
        def emit_table():
            if len(run)<2:return
            counts['function_pointer_table_candidates']+=1
            rtti=None;prev=pe.read(run_start-8,8)
            if prev:
                col=int.from_bytes(prev,'little');raw=pe.read(col,24)
                if raw:
                    signature,offset,cd,td,ch,selfrva=struct.unpack('<6I',raw)
                    typename=pe.string(pe.base+td+16)
                    if signature==1 and pe.base+selfrva==col and typename.startswith(('.?AV','.?AU')):
                        rtti=dict(locator=f'{col:x}',type_descriptor=f'{pe.base+td:x}',name=typename,
                                  object_offset=offset,hierarchy=f'{pe.base+ch:x}')
                        counts['tables_with_structural_rtti']+=1
            out.emit(dict(kind='function_pointer_table_candidate', address=f'{run_start:x}',
                entries=[dict(slot=i,target=f'{v:x}',name=names[v]) for i,v in enumerate(run)],rtti=rtti))
        for offset in range((-s['start'])%8,max(0,len(blob)-7),8):
            target=struct.unpack_from('<Q',blob,offset)[0]
            if target in names:
                if not run:run_start=s['start']+offset
                run.append(target)
            elif run:emit_table();run=[]
        if run:emit_table()
    for start,end,info in pe.unwind_entries():
        counts['runtime_function_entries']+=1
        if start not in names:
            counts['runtime_entries_without_function_start']+=1
            out.emit(dict(kind='unwind_entry_without_function_start',start=f'{start:x}',end_exclusive=f'{end:x}',
                          unwind=f'{info:x}',interpretation='may_be_function_fragment_or_discovery_gap'))
    with sqlite3.connect(Path(spec['database']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        for address,name,signature,text in db.execute('SELECT address,name,signature,reference_text FROM functions WHERE sha256=?',(spec['sha256'],)):
            counts['functions_scanned_for_library_evidence']+=1
            labels=[k for k,p in PATTERNS.items() if p.search(name+' '+signature)]
            references=[]
            for family,p in PATTERNS.items():
                m=p.search(text or '')
                if m:references.append(dict(library=family,excerpt=text[max(0,m.start()-60):m.end()+140]))
            if labels or references:
                counts['functions_with_library_evidence']+=1
                out.emit(dict(kind='function_library_evidence',address=address,name=name,
                              named_families=labels,referenced_markers=references,
                              reference_markers_do_not_establish_ownership=True))
    if spec.get('gaps'):
        with open(spec['gaps']) as stream:
            header=json.loads(next(stream))
            if (header['sha256'],header['snapshot_id'])!=(spec['sha256'],spec['snapshot_id']):
                raise ValueError('Gap audit identity mismatch')
            ended=False
            for line in stream:
                row=json.loads(line)
                if ended:raise ValueError('Gap data after completion')
                if row['kind']=='complete':ended=True;continue
                counts['gap:'+row['kind']]+=1
                if row.get('only_00_90_cc_bytes'):counts['padding_pattern_ranges']+=1
                out.emit(dict(row, source_audit=spec['gaps']))
            if not ended:raise ValueError('Incomplete gap input')
    out.finish(dict(counts));return dict(counts)


def run(args):
    root=args.directory.resolve();root.mkdir(parents=True,exist_ok=False)
    q=json.loads(args.queue.read_text());catalog=json.loads((args.export/'catalog.json').read_text())
    jobs=[];specs={}
    for entry in q['programs']:
        if entry['state']!='complete':continue
        gap=next(j for j in q['jobs'].values() if j['kind']=='gaps' and j['program']['sha256']==entry['sha256'])
        spec=dict(sha256=entry['sha256'],snapshot_id=gap['snapshot_id'],binary_path=entry['program']['binary_path'],
                  database=entry['database'],gaps=gap['result']['output'])
        specs[entry['sha256']]=spec
    manifest=json.loads((args.export/'manifest.json').read_text());p=manifest['programs'][0]
    spec=dict(sha256=p['sha256'],snapshot_id=manifest['snapshot_id'],binary_path=p['binary_path'],database=str(args.export/'corpus.sqlite'))
    specs[p['sha256']]=spec
    for sha,spec in specs.items():jobs.append(dict(spec,kind='binary',output=str(root/(sha[:16]+'.binary.jsonl'))))
    for j in q['jobs'].values():
        if j['kind']=='indirect' and j['state']=='complete':
            jobs.append(dict(specs[j['program']['sha256']],kind='indirect',source=j['result']['output'],
                             output=str(root/(j['id']+'.targets.jsonl'))))
    for path in sorted(args.indirect.glob('*/evidence.jsonl')):
        jobs.append(dict(specs[p['sha256']],kind='indirect',source=str(path),output=str(root/('agent.'+path.parent.name+'.targets.jsonl'))))
    atomic_json(root/'plan.json',jobs)
    status=dict(state='running',complete=False,jobs=len(jobs),completed=0,failed=0,workers=args.workers,totals={})
    atomic_json(root/'status.json',status);totals=Counter();results=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending={pool.submit(indirect if j['kind']=='indirect' else binary,j):j for j in jobs}
        for future in as_completed(pending):
            j=pending[future]
            try:
                counts=future.result();totals.update(counts);status['completed']+=1
                results.append(dict(output=j['output'],kind=j['kind'],sha256=j['sha256'],summary=counts))
            except Exception as error:
                status['failed']+=1;results.append(dict(output=j['output'],error=str(error)))
            status['totals']=dict(totals);atomic_json(root/'status.json',status);atomic_json(root/'catalog.json',results)
    status.update(state='complete' if not status['failed'] else 'blocked_or_failed',complete=not status['failed'])
    atomic_json(root/'status.json',status)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('queue','export','indirect','directory'):parser.add_argument(name,type=Path)
    parser.add_argument('--workers',type=int,default=42)
    run(parser.parse_args())
