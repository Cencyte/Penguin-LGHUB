"""Only complete indexed coverage triggers 100%; decompilation exceptions stay visible."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from notify_completion import completion_summary


class CompletionTests(unittest.TestCase):
    def fixture(self, root):
        path = root / 'corpus.sqlite'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE functions(sha256 TEXT); CREATE TABLE coverage(sha256 TEXT, metadata TEXT);')
            db.executemany('INSERT INTO functions VALUES(?)', [('binary',)] * 3)
            db.execute('INSERT INTO coverage VALUES(?,?)', ('binary', json.dumps(dict(attempted=3, ok=2, failed=1, timeout=0))))
        return dict(sha256='binary', state='complete', database=str(path), coverage=dict(
            sha256='binary', export_complete=True, recognized_internal_functions=3, shards=1,
            outcomes=dict(ok=2, failed=1)))

    def test_incomplete_pipeline_never_claims_complete(self):
        self.assertIsNone(completion_summary([dict(sha256='binary', state='waiting_external')], {'binary'}))

    def test_scope_must_match_exactly(self):
        with self.assertRaises(ValueError):
            completion_summary([dict(sha256='binary', state='complete')], {'binary', 'missing'})
        with self.assertRaises(ValueError):
            completion_summary([], set())

    def test_complete_export_keeps_failed_decompilation_count(self):
        with tempfile.TemporaryDirectory() as directory:
            program = self.fixture(Path(directory))
            result = completion_summary([program], {'binary'})
            self.assertEqual(result, dict(programs=1, export_percent=100, functions=3, ok=2, failed=1, timeout=0))

    def test_missing_index_or_mismatched_counts_cannot_notify_success(self):
        with tempfile.TemporaryDirectory() as directory:
            program = self.fixture(Path(directory))
            program['coverage']['recognized_internal_functions'] = 4
            with self.assertRaises(ValueError):
                completion_summary([program], {'binary'})
            path = Path(program['database']); path.unlink()
            with self.assertRaises(sqlite3.OperationalError):
                completion_summary([program], {'binary'})
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
