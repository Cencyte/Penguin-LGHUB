#!/usr/bin/env python3
"""DragonShard-2: strict export coverage, SQLite FTS, and bounded evidence retrieval."""
import argparse
import collections
import json
from pathlib import Path
import sqlite3
import sys


def rows(path):
    with Path(path).open() as stream:
        for line, raw in enumerate(stream, 1):
            try:
                yield json.loads(raw)
            except ValueError as exc:
                raise ValueError(f'{path}:{line}: invalid JSON') from exc


def inspect(path):
    """Reject incomplete/mispartitioned exports, not just JSON syntax failures."""
    if str(path).endswith('.partial'):
        raise ValueError(f'Partial export: {path}')
    stream = iter(rows(path))
    header = next(stream, {})
    if header.get('kind') != 'program' or header.get('schema') != 2:
        raise ValueError(f'Not a schema-2 export: {path}')
    n, shard = header['shards'], header['shard']
    if n < 1 or not 0 <= shard < n:
        raise ValueError('Invalid shard partition')
    counts = collections.Counter()
    ordinals, addresses = set(), set()
    end = None
    for row in stream:
        if end is not None:
            raise ValueError('Records after completion marker')
        kind = row['kind']
        if kind == 'function':
            ordinal, address, status = row['ordinal'], row['address'], row['status']
            if ordinal < 0 or ordinal % n != shard or ordinal in ordinals or address in addresses:
                raise ValueError(f'Duplicate or incorrectly partitioned function in {path}')
            if status not in ('ok', 'failed', 'timeout') or (status == 'ok' and not row.get('c')):
                raise ValueError(f'Invalid function outcome in {path}')
            ordinals.add(ordinal)
            addresses.add(address)
            counts[status] += 1
        elif kind == 'symbol':
            if shard != 0:
                raise ValueError('Symbols belong to shard zero')
        elif kind == 'complete':
            end = row
        else:
            raise ValueError(f'Unexpected record: {kind}')
    if end is None or end['attempted'] != len(ordinals):
        raise ValueError(f'Missing/incorrect completion record: {path}')
    if any(end[k] != counts[k] for k in ('ok', 'failed', 'timeout')):
        raise ValueError(f'Outcome totals disagree: {path}')
    if ordinals != set(range(shard, end['total_enumerated'], n)):
        raise ValueError(f'Missing function ordinals: {path}')
    return header, end


SCHEMA = '''
CREATE TABLE programs(sha256 TEXT PRIMARY KEY, name TEXT, metadata TEXT);
CREATE TABLE functions(id INTEGER PRIMARY KEY, sha256 TEXT, address TEXT, rva TEXT,
 ordinal INTEGER, name TEXT, signature TEXT, status TEXT, c TEXT, error TEXT,
 reference_text TEXT, metadata TEXT, UNIQUE(sha256,address), UNIQUE(sha256,ordinal));
CREATE TABLE symbols(sha256 TEXT, address TEXT, name TEXT, metadata TEXT);
CREATE INDEX symbol_address ON symbols(sha256,address);
CREATE TABLE refs(sha256 TEXT, function_address TEXT, source TEXT, target TEXT,
 type TEXT, is_call INTEGER, callee TEXT, symbol TEXT, metadata TEXT);
CREATE INDEX ref_caller ON refs(sha256,function_address);
CREATE INDEX ref_callee ON refs(sha256,callee);
CREATE INDEX ref_target ON refs(sha256,target);
CREATE VIRTUAL TABLE search USING fts5(name, signature, c, reference_text,
 content='functions', content_rowid='id');
CREATE TABLE coverage(sha256 TEXT, shard INTEGER, source_file TEXT, metadata TEXT,
 UNIQUE(sha256,shard));
'''


def build(paths, destination, expected=None, *, max_bytes=None):
    paths = sorted(Path(p).resolve() for p in paths)
    if not paths:
        raise ValueError('No completed exports supplied')
    groups = {}
    for path in paths:
        h, end = inspect(path)
        group = groups.setdefault(h['sha256'], [])
        group.append((path, h, end))
    if expected is not None and set(groups) != set(expected):
        raise ValueError('Exported binary hashes do not match the entire expected manifest scope')
    for sha, group in groups.items():
        first = group[0][1]
        n, total = first['shards'], group[0][2]['total_enumerated']
        ids = [h['shard'] for _, h, _ in group]
        if sorted(ids) != list(range(n)):
            raise ValueError(f'Missing/duplicate shards for {first["name"]}: {ids} of {n}')
        for _, h, end in group:
            keys = ('name', 'project_path', 'image_base', 'language', 'ghidra', 'shards', 'reported_function_count', 'snapshot_id')
            if any(h.get(k) != first.get(k) for k in keys) or end['total_enumerated'] != total:
                raise ValueError(f'Inconsistent program identity/analysis across shards: {sha}')
        if expected is not None and expected[sha]['project_path'] != first['project_path']:
            raise ValueError(f'Wrong Ghidra program path for {sha}')
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.partial')
    if partial.exists():
        raise ValueError(f'Interrupted index exists; inspect/remove explicitly first: {partial}')
    db = sqlite3.connect(partial)
    report = []
    try:
        if max_bytes is not None:
            pages = int(max_bytes) // db.execute('PRAGMA page_size').fetchone()[0]
            if pages < 1:
                raise ValueError('Index output budget must hold at least one page')
            db.execute(f'PRAGMA max_page_count={pages}')
            # This is a new disposable index; raw exports remain the source of truth.
            # Avoid an unbounded rollback journal/temp file beside the capped database.
            db.execute('PRAGMA journal_mode=OFF')
            db.execute('PRAGMA temp_store=MEMORY')
        db.executescript(SCHEMA)
        for sha, group in groups.items():
            first = group[0][1]
            db.execute('INSERT INTO programs VALUES(?,?,?)', (sha, first['name'], json.dumps(first)))
            counts = collections.Counter()
            listing = collections.Counter()
            for path, header, end in group:
                db.execute('INSERT INTO coverage VALUES(?,?,?,?)', (sha, header['shard'], str(path), json.dumps(end)))
                for row in rows(path):
                    kind = row['kind']
                    if kind == 'symbol':
                        db.execute('INSERT INTO symbols VALUES(?,?,?,?)', (sha, row['address'], row['name'], json.dumps(row)))
                    elif kind == 'function':
                        counts[row['status']] += 1
                        if 'instruction_count' in row:
                            listing['functions_with_instruction_metadata'] += 1
                            listing['zero_instruction_functions'] += int(row['instruction_count'] == 0)
                            listing['synthetic_external_thunks'] += int(row.get('synthetic_external_thunk', False))
                            listing['function_instruction_visits'] += row['instruction_count']
                        refs = row.get('references', [])
                        listing['functions_with_references'] += int(bool(refs))
                        listing['instruction_references'] += len(refs)
                        listing['unresolved_indirect_call_records'] += sum(r['type'] == 'unresolved_indirect_call' for r in refs)
                        meta = {k: v for k, v in row.items() if k not in ('c', 'reference_text', 'references')}
                        db.execute('INSERT INTO functions VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?)',
                                   (sha, row['address'], row.get('rva'), row['ordinal'], row['name'], row.get('signature', ''),
                                    row['status'], row.get('c', ''), row.get('error', ''), row.get('reference_text', ''), json.dumps(meta)))
                        for ref in row.get('references', []):
                            db.execute('INSERT INTO refs VALUES(?,?,?,?,?,?,?,?,?)',
                                       (sha, row['address'], ref['from'], ref.get('to'), ref['type'], int(ref.get('call', False)),
                                        ref.get('callee'), ref.get('symbol'), json.dumps(ref)))
                db.commit()
            report.append({'name': first['name'], 'sha256': sha,
                           'recognized_internal_functions': sum(counts.values()), 'outcomes': dict(counts),
                           'listing_evidence': dict(listing),
                           'shards': len(group), 'export_complete': True,
                           'all_functions_decompiled': not (counts['failed'] or counts['timeout'])})
        db.execute("INSERT INTO search(search) VALUES('rebuild')")
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        db.close()
        partial.replace(destination)
    except BaseException:
        db.close()
        # This is our own uncommitted index, not raw evidence.
        partial.unlink(missing_ok=True)
        raise
    return report


def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def search(db, expression, limit=10):
    return [dict(r) for r in db.execute('''SELECT p.name AS binary, f.sha256, f.address, f.rva, f.name,
     f.status, snippet(search,2,'[',']',' … ',32) AS excerpt,
     snippet(search,3,'[',']',' … ',24) AS reference_excerpt
     FROM search JOIN functions f ON f.id=search.rowid JOIN programs p ON p.sha256=f.sha256
     WHERE search MATCH ? ORDER BY bm25(search) LIMIT ?''', (expression, limit))]


def search_symbols(db, symbols, expression, limit=10):
    """Join a separately verified PDB overlay to this corpus by binary hash/address.

    The export may still call a function FUN_... even when the PDB supplies its
    name. Keep both identities and leave the original decompilation untouched.
    Data symbols and symbols from other binary versions cannot become C hits.
    """
    uri = Path(symbols).resolve().as_uri() + '?mode=ro'
    db.execute('ATTACH DATABASE ? AS pdb_symbols', (uri,))
    try:
        return [dict(r) for r in db.execute('''
         SELECT p.name AS binary, f.sha256, f.address, f.rva, f.name, f.status,
                s.name AS pdb_name, s.demangled AS pdb_demangled,
                substr(f.c,1,1200) AS excerpt, length(f.c)>1200 AS c_truncated
         FROM pdb_symbols.search AS s
         JOIN functions f ON f.sha256=s.binary_sha256 AND f.address=s.address
         JOIN programs p ON p.sha256=f.sha256
         WHERE s.search MATCH ? ORDER BY bm25(s.search) LIMIT ?''', (expression, limit))]
    finally:
        db.execute('DETACH DATABASE pdb_symbols')


def function(db, sha, address, max_chars=12000):
    row = db.execute('SELECT * FROM functions WHERE sha256=? AND address=?', (sha, address)).fetchone()
    if row is None:
        raise ValueError('Function not found; supply the full binary SHA256 and Ghidra address')
    result = dict(row)
    result['c_truncated'] = len(result['c']) > max_chars
    result['c'] = result['c'][:max_chars]
    result['reference_text_truncated'] = len(result['reference_text']) > max_chars
    result['reference_text'] = result['reference_text'][:max_chars]
    for key, sql in (
        ('outgoing', 'SELECT * FROM refs WHERE sha256=? AND function_address=? LIMIT 101'),
        ('callers', 'SELECT * FROM refs WHERE sha256=? AND callee=? AND is_call=1 LIMIT 101'),
        ('incoming_references', 'SELECT * FROM refs WHERE sha256=? AND target=? LIMIT 101'),
    ):
        hits = [dict(r) for r in db.execute(sql, (sha, address))]
        result[key + '_truncated'] = len(hits) > 100
        result[key] = []
        for hit in hits[:100]:
            # Raw records remain intact in the database/JSONL; retrieval must stay bounded.
            hit['metadata_truncated'] = len(hit['metadata']) > 1000
            hit['metadata'] = hit['metadata'][:1000]
            result[key].append(hit)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    idx = sub.add_parser('index')
    idx.add_argument('exports', type=Path)
    idx.add_argument('database', type=Path)
    idx.add_argument('--manifest', type=Path, required=True, help='Enforce the complete selected corpus')
    query = sub.add_parser('search')
    query.add_argument('database')
    query.add_argument('expression')
    query.add_argument('--limit', type=int, default=10, choices=range(1, 51), metavar='1..50')
    catalog = sub.add_parser('search-catalog')
    catalog.add_argument('catalog', type=Path)
    catalog.add_argument('expression')
    catalog.add_argument('--limit-per-batch', type=int, default=5, choices=range(1, 26), metavar='1..25')
    catalog.add_argument('--symbols', type=Path,
                         help='Also search the verified PDB overlay, joined by exact binary hash and function address')
    get = sub.add_parser('function')
    get.add_argument('database')
    get.add_argument('sha256')
    get.add_argument('address')
    get.add_argument('--max-chars', type=int, default=12000, choices=range(100, 100001), metavar='100..100000')
    fail = sub.add_parser('failures')
    fail.add_argument('database')
    fail.add_argument('--limit', type=int, default=100, choices=range(1, 1001), metavar='1..1000')
    args = parser.parse_args()
    if args.command == 'index':
        manifest = json.loads(args.manifest.read_text())
        expected = {p['sha256']: p for p in manifest['programs']}
        result = build(args.exports.glob('*.jsonl'), args.database, expected)
    elif args.command == 'search-catalog':
        catalog = json.loads(args.catalog.read_text())
        if len(catalog['databases']) > 21:
            raise ValueError('Catalog exceeds bounded retrieval limit of 21 databases')
        symbols = args.symbols or catalog.get('symbol_database')
        result = {'corpus_complete': catalog['complete'], 'batches': []}
        if symbols:
            result['symbol_database'] = str(Path(symbols).resolve())
        for path in catalog['databases']:
            db = connect(path)
            try:
                batch = {'database': path, 'hits': search(db, args.expression, args.limit_per_batch)}
                if symbols:
                    batch['symbol_hits'] = search_symbols(db, symbols, args.expression, args.limit_per_batch)
                result['batches'].append(batch)
            finally:
                db.close()
    else:
        db = connect(args.database)
        try:
            if args.command == 'search':
                result = search(db, args.expression, args.limit)
            elif args.command == 'function':
                result = function(db, args.sha256, args.address, args.max_chars)
            else:
                result = [dict(r) for r in db.execute("SELECT p.name AS binary,f.address,f.rva,f.name,f.status,f.error FROM functions f JOIN programs p USING(sha256) WHERE status!='ok' LIMIT ?", (args.limit,))]
        finally:
            db.close()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, sqlite3.Error, OSError) as error:
        sys.exit(str(error))
