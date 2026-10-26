"""Coverage failures must not masquerade as a complete research corpus."""
import json
import sqlite3
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from corpus import build, connect, function, inspect, search, search_symbols


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.header = dict(kind='program', schema=2, sha256='a' * 64, name='fixture',
                           project_path='/fixture', image_base='1000', language='x86:LE:64:default',
                           ghidra='12.1.2', shards=2, reported_function_count=4)

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, shard, statuses=('ok', 'ok')):
        records = [dict(self.header, shard=shard)]
        for ordinal, status in zip(range(shard, 4, 2), statuses):
            records.append(dict(kind='function', ordinal=ordinal, address=str(1000 + ordinal), rva=str(ordinal),
                                name='entry' + str(ordinal), signature='int entry(void)', status=status,
                                instruction_count=2,
                                c='int entry(void) { return EvidenceToken; }' if status == 'ok' else '',
                                error='' if status == 'ok' else 'explicit test failure', reference_text='GUID_Evidence',
                                references=[dict(type='UNCONDITIONAL_CALL', call=True, **{'from': str(1000 + ordinal), 'to': '1001', 'callee': '1001'})]))
        records.append(dict(kind='complete', total_enumerated=4, attempted=2,
                            ok=statuses.count('ok'), failed=statuses.count('failed'), timeout=statuses.count('timeout')))
        path = self.root / f'{shard}.jsonl'
        path.write_text(''.join(json.dumps(r) + '\n' for r in records))
        return path

    def test_retrieval_and_call_graph(self):
        files = [self.export(0), self.export(1)]
        report = build(files, self.root / 'corpus.sqlite', {self.header['sha256']: self.header})
        self.assertEqual(report[0]['recognized_internal_functions'], 4)
        self.assertEqual(report[0]['listing_evidence']['functions_with_instruction_metadata'], 4)
        self.assertEqual(report[0]['listing_evidence']['zero_instruction_functions'], 0)
        self.assertEqual(report[0]['listing_evidence']['instruction_references'], 4)
        db = connect(self.root / 'corpus.sqlite')
        self.assertEqual(len(search(db, 'EvidenceToken')), 4)
        self.assertEqual(len(search(db, 'GUID_Evidence', 1)), 1)
        result = function(db, self.header['sha256'], '1001', 5)
        self.assertTrue(result['c_truncated'])
        self.assertEqual(len(result['callers']), 4)
        db.close()

    def test_overlay_finds_unnamed_functions_only_in_the_matching_binary(self):
        files = [self.export(0, ('ok', 'failed')), self.export(1)]
        build(files, self.root / 'corpus.sqlite')
        overlay = self.root / 'symbols.sqlite'
        with sqlite3.connect(overlay) as symbols:
            symbols.execute('CREATE VIRTUAL TABLE search USING fts5(binary_sha256 UNINDEXED,address UNINDEXED,name,demangled)')
            symbols.executemany('INSERT INTO search VALUES(?,?,?,?)', [
                ('a' * 64, '1000', '?ProcessWakeCompletion', 'RegisteredWait::ProcessWakeCompletion'),
                ('b' * 64, '1001', '?WrongVersion', 'RegisteredWait::WrongVersion'),
                ('a' * 64, '9000', '?DataSymbol', 'RegisteredWait::DataSymbol'),
                ('a' * 64, '1002', '?FailedBody', 'RegisteredWait::FailedBody'),
            ])
        original = overlay.read_bytes()
        db = connect(self.root / 'corpus.sqlite')
        try:
            self.assertEqual(search(db, 'RegisteredWait'), [])
            hits = search_symbols(db, overlay, 'RegisteredWait')
            self.assertEqual({h['address'] for h in hits}, {'1000', '1002'})
            self.assertEqual({h['status'] for h in hits}, {'ok', 'failed'})
            self.assertTrue(all(h['sha256'] == 'a' * 64 for h in hits))
            self.assertEqual(len(search_symbols(db, overlay, 'RegisteredWait', 1)), 1)
            self.assertEqual(overlay.read_bytes(), original)
            self.assertNotIn('pdb_symbols', [r[1] for r in db.execute('PRAGMA database_list')])
        finally:
            db.close()

        catalog = self.root / 'catalog.json'
        catalog.write_text(json.dumps(dict(complete=True, databases=[str(self.root / 'corpus.sqlite')],
                                          symbol_database=str(overlay))))
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/corpus.py'),
                                 'search-catalog', str(catalog), 'RegisteredWait', '--limit-per-batch', '1'],
                                check=True, capture_output=True, text=True)
        found = json.loads(result.stdout)
        self.assertEqual(found['symbol_database'], str(overlay))
        self.assertEqual(len(found['batches'][0]['symbol_hits']), 1)

    def test_missing_symbol_overlay_is_not_created(self):
        build([self.export(0), self.export(1)], self.root / 'corpus.sqlite')
        missing = self.root / 'missing.sqlite'
        db = connect(self.root / 'corpus.sqlite')
        try:
            with self.assertRaises(sqlite3.OperationalError):
                search_symbols(db, missing, 'RegisteredWait')
            self.assertFalse(missing.exists())
        finally:
            db.close()

    def test_missing_or_duplicate_shard(self):
        one = self.export(0)
        for files in ([one], [one, one]):
            with self.assertRaisesRegex(ValueError, 'Missing/duplicate'):
                build(files, self.root / 'broken.sqlite')

    def test_index_size_cap_preserves_raw_evidence_and_previous_index(self):
        import sqlite3
        files = [self.export(0), self.export(1)]
        target = self.root / 'corpus.sqlite'
        build(files, target)
        original = target.read_bytes()
        with self.assertRaisesRegex(sqlite3.DatabaseError, 'full'):
            build(files, target, max_bytes=4096)
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue(all(path.exists() for path in files))
        self.assertFalse(target.with_suffix('.sqlite.partial').exists())

    def test_bounded_index_can_be_searched_and_stays_inside_cap(self):
        files = [self.export(0), self.export(1)]
        target = self.root / 'bounded.sqlite'
        build(files, target, max_bytes=1024 * 1024)
        self.assertLessEqual(target.stat().st_size, 1024 * 1024)
        db = connect(target)
        self.assertEqual(len(search(db, 'EvidenceToken')), 4)
        db.close()

    def test_interrupted_export(self):
        p = self.export(0)
        p.write_text('\n'.join(p.read_text().splitlines()[:-1]) + '\n')
        with self.assertRaisesRegex(ValueError, 'completion'):
            inspect(p)

    def test_wrong_partition(self):
        p = self.export(0)
        lines = p.read_text().splitlines()
        row = json.loads(lines[1]); row['ordinal'] = 1; lines[1] = json.dumps(row)
        p.write_text('\n'.join(lines) + '\n')
        with self.assertRaisesRegex(ValueError, 'partitioned'):
            inspect(p)

    def test_failures_are_counted_not_hidden(self):
        files = [self.export(0, ('timeout', 'failed')), self.export(1)]
        report = build(files, self.root / 'partial_decompilation.sqlite')
        self.assertTrue(report[0]['export_complete'])
        self.assertFalse(report[0]['all_functions_decompiled'])
        self.assertEqual(report[0]['outcomes'], {'timeout': 1, 'failed': 1, 'ok': 2})

    def test_missing_program(self):
        files = [self.export(0), self.export(1)]
        expected = {self.header['sha256']: self.header, 'b' * 64: dict(self.header)}
        with self.assertRaisesRegex(ValueError, 'entire expected manifest'):
            build(files, self.root / 'missing_program.sqlite', expected)

    def test_inconsistent_program_state(self):
        files = [self.export(0), self.export(1)]
        lines = files[1].read_text().splitlines()
        h = json.loads(lines[0]); h['reported_function_count'] = 5; lines[0] = json.dumps(h)
        files[1].write_text('\n'.join(lines) + '\n')
        with self.assertRaisesRegex(ValueError, 'Inconsistent'):
            build(files, self.root / 'wrong.sqlite')

    def test_duplicate_address_across_shards_does_not_replace_good_index(self):
        files = [self.export(0), self.export(1)]
        target = self.root / 'corpus.sqlite'
        build(files, target)
        original = target.read_bytes()
        lines = files[1].read_text().splitlines()
        row = json.loads(lines[1]); row['address'] = '1000'; lines[1] = json.dumps(row)
        files[1].write_text('\n'.join(lines) + '\n')
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            build(files, target)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(target.with_suffix('.sqlite.partial').exists())


if __name__ == '__main__':
    unittest.main()
