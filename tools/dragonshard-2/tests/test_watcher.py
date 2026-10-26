"""The watcher must not confuse symbol rows or a truncated tail with completed functions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from watch_campaign import shard_progress


class WatcherProgressTests(unittest.TestCase):
    def write_rows(self, path, rows):
        path.write_text(''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in rows))

    def test_partial_ordinal_counts_only_its_partition(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'part.jsonl.partial'
            self.write_rows(path, [{'kind': 'program', 'shards': 4},
                                  {'kind': 'symbol', 'name': 'unrelated'},
                                  {'kind': 'function', 'ordinal': 17}])
            self.assertEqual(shard_progress(path), (5, False))

    def test_completion_uses_exact_attempted_count(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'part.jsonl'
            self.write_rows(path, [{'kind': 'program', 'shards': 4},
                                  {'kind': 'function', 'ordinal': 17},
                                  {'kind': 'complete', 'attempted': 5}])
            self.assertEqual(shard_progress(path), (5, True))

    def test_recovery_counts_only_new_segment_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'segment.jsonl.partial'
            self.write_rows(path, [{'kind': 'program', 'shards': 4, 'shard': 1, 'segment': True, 'ordinal_start': 101},
                                  {'kind': 'function', 'ordinal': 101},
                                  {'kind': 'function', 'ordinal': 105}])
            self.assertEqual(shard_progress(path), (2, False))
            with path.open('a') as out:
                out.write('{"kind":"function","ordinal":109,"c":"interrupted')
            self.assertEqual(shard_progress(path), (2, False))

    def test_large_function_tail_preserves_last_known_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'part.jsonl.partial'
            self.write_rows(path, [{'kind': 'program', 'shards': 4},
                                  {'kind': 'function', 'ordinal': 17, 'c': 'x' * 100000}])
            self.assertEqual(shard_progress(path, 4), (4, False))


if __name__ == '__main__':
    unittest.main()
