#!/usr/bin/env python3
"""Exercise the real packed-snapshot queue executors in Ghidra, without raw DB clones."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dragonshard import atomic_json, digest
from swarm_tasks import execute


def run(args):
    manifest = json.loads(args.manifest.read_text())
    program = manifest['programs'][0]
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    packed = args.packed.resolve()
    snapshot = digest(packed)
    base = dict(manifest=manifest, program=program, work=str(root), cpu=1,
        packed_file=str(packed), packed_sha256=snapshot, packed_cache=str(root / 'cache'),
        snapshot_id=snapshot, source_dir=manifest['project_dir'], source_name=manifest['project_name'],
        output_limit_bytes=64 * 1024**2)
    warm = execute(dict(base, kind='warm', id='fixture.warm'))
    jobs = [dict(base, kind='export', id=f'fixture.export.{i}', shard=i, shards=2) for i in range(2)]
    jobs += [dict(base, kind='gaps', id='fixture.gaps')]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(execute, jobs))
    functions = {}
    for line in Path(results[-1]['output']).read_text().splitlines():
        row = json.loads(line)
        if row['kind'] == 'unresolved_computed_transfer' and row.get('function'):
            functions.setdefault(row['function'], []).append(row['address'])
    if not functions:
        first = next(json.loads(line) for line in Path(results[0]['output']).read_text().splitlines()
                     if json.loads(line)['kind'] == 'function')
        functions[first['address']] = []
    indirect = execute(dict(base, kind='indirect', id='fixture.indirect',
        functions=[dict(address=address, sites=sites) for address, sites in functions.items()]))
    indexed = execute(dict(base, kind='index', id='fixture.index'))
    assert digest(packed) == snapshot
    assert sum(r['attempted'] for r in results[:2]) == indexed['coverage'][0]['recognized_internal_functions']
    assert not any((root / 'clones').iterdir())
    atomic_json(root / 'results.json', dict(complete=True, warm=warm, exports_and_gaps=results,
        indirect=indirect, index=indexed, packed_snapshot_unchanged=True))
    print(json.dumps(indexed))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('packed', type=Path)
    parser.add_argument('directory', type=Path)
    run(parser.parse_args())
