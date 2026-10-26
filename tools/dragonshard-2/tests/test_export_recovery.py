import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from packed_export import shard_budget
from recover_packed_export import prefix, split_tails


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'export.jsonl.partial'
        self.header = dict(kind='program', schema=2, sha256='a'*64, snapshot_id='b'*64,
                           shards=2, shard=1, reported_function_count=10)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, ordinals, tail=b''):
        rows = [self.header, *[dict(kind='function', ordinal=n, address=str(n), status='ok', c='code') for n in ordinals]]
        self.path.write_bytes(b''.join((json.dumps(row)+'\n').encode() for row in rows)+tail)

    def test_truncated_last_record_is_not_adopted(self):
        self.write([1, 3], b'{"kind":"function","ordinal":5')
        p = prefix(self.path, dict(sha256='a'*64))
        self.assertEqual((p['functions'], p['next_ordinal']), (2, 5))

    def test_reject_identity_gaps_and_duplicates(self):
        self.write([1, 3])
        with self.assertRaises(ValueError):
            prefix(self.path, dict(snapshot_id='c'*64))
        for ordinals in ([1, 5], [1, 1]):
            self.write(ordinals)
            with self.assertRaises(ValueError):
                prefix(self.path, {})

    def test_all_missing_ordinals_exactly_once(self):
        ps = {0: dict(header=dict(shards=2), next_ordinal=4), 1: dict(header=dict(shards=2), next_ordinal=7)}
        jobs = split_tails(ps, 30, 4)
        found = [n for j in jobs for n in range(j['ordinal_start'],j['ordinal_end'],2)]
        expected = list(range(4,30,2))+list(range(7,30,2))
        self.assertEqual(sorted(found), sorted(expected))
        self.assertEqual(len(found),len(set(found)))

    def test_segment_requires_exact_completion(self):
        self.header.update(segment=True, ordinal_start=5, ordinal_end=9)
        self.write([5,7])
        bounds=dict(ordinal_start=5,ordinal_end=9)
        with self.assertRaisesRegex(ValueError,'did not complete'):
            prefix(self.path,{},segment=bounds)
        with self.path.open('a') as out:
            out.write(json.dumps(dict(kind='complete',total_enumerated=10,attempted=2,ok=2,failed=0,timeout=0))+'\n')
        self.assertEqual(prefix(self.path,{},segment=bounds)['functions'],2)

    def test_symbol_budget_is_extra_without_exceeding_total(self):
        limits=[shard_budget(8000,42,i) for i in range(42)]
        self.assertGreater(limits[0],limits[1]*5)
        self.assertLessEqual(sum(limits),8000)

    def test_native_iterator_count_excludes_external_functions(self):
        self.header.update(segment=True, ordinal_start=5, ordinal_end=9, reported_function_count=12)
        self.write([5,7])
        with self.path.open('a') as out:
            out.write(json.dumps(dict(kind='complete',total_enumerated=10,attempted=2,ok=2,failed=0,timeout=0))+'\n')
        result=prefix(self.path,{},segment=dict(ordinal_start=5,ordinal_end=9),total=10)
        self.assertEqual(result['functions'],2)
