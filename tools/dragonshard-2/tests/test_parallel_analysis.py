"""Partition coverage and balancing for independent analysis databases."""
from pathlib import Path
import sys
import unittest
import tempfile
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from parallel_analysis import partition, packing_slots, validate_worker, cgroup_packing_headroom, load_width_policy, GIB


class AnalysisPartitionTests(unittest.TestCase):
    def test_every_function_exactly_once(self):
        functions = [dict(address=f'{i:08x}', body_bytes=(i * 19) % 997 + 1) for i in range(3513)]
        shards = partition(functions, 42)
        addresses = [address for shard in shards for address in shard]
        self.assertEqual(len(shards), 42)
        self.assertEqual(len(addresses), len(functions))
        self.assertEqual(set(addresses), {f['address'] for f in functions})

    def test_large_functions_distribute_across_workers(self):
        functions = [dict(address=str(i), body_bytes=10000 if i < 4 else 1) for i in range(100)]
        shards = partition(functions, 4)
        for shard in shards:
            self.assertEqual(len(set(shard).intersection({'0', '1', '2', '3'})), 1)

    def test_reproducible_and_no_empty_workers(self):
        functions = [dict(address=str(i), body_bytes=10) for i in range(3)]
        self.assertEqual(partition(functions, 42), partition(list(reversed(functions)), 42))
        self.assertEqual(len(partition(functions, 42)), 3)


class PipelineTests(unittest.TestCase):
    def test_reviewed_width_policy_requires_exact_identity_bytes_and_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.json'
            entry = dict(address='143dc9d10', hex='47006c006f006200', reason='Reviewed repeated width-only cycle')
            valid = dict(schema=1, sha256='binary', reviewed_untyped_widths=[entry])
            path.write_text(json.dumps(valid))
            self.assertEqual(load_width_policy(path, 'binary'), [entry])
            self.assertEqual(load_width_policy(None, 'binary'), [])
            for mutation in [dict(sha256='other'), dict(schema=2), dict(reviewed_untyped_widths=[]),
                             *[dict(reviewed_untyped_widths=[dict(entry, **change)]) for change in
                               [dict(address='../elsewhere'), dict(hex='0'), dict(hex='gg'),
                                dict(hex='aa' * 4097), dict(reason='')]]]:
                path.write_text(json.dumps(dict(valid, **mutation)))
                with self.assertRaises(ValueError):
                    load_width_policy(path, 'binary')

    def test_save_admission_respects_throttle_and_ancestor_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); parent = base / 'parent'; child = parent / 'worker'; child.mkdir(parents=True)
            for group, current, high, maximum in [(parent, 40, 60, 80), (child, 30, 40, 66)]:
                for name, value in [('memory.current', current), ('memory.high', high), ('memory.max', maximum)]:
                    (group / name).write_text(str(value * GIB))
            self.assertEqual(cgroup_packing_headroom(child, base), 8 * GIB)
            (child / 'memory.high').write_text('max')
            self.assertEqual(cgroup_packing_headroom(child, base), 18 * GIB)
            (parent / 'memory.current').write_text(str(59 * GIB))
            self.assertEqual(cgroup_packing_headroom(child, base), 0)

    def test_saves_expand_to_all_workers_when_space_permits(self):
        self.assertEqual(packing_slots(1000, 100, 10, 42), 42)
        self.assertEqual(packing_slots(170, 100, 10, 42), 4)
        self.assertEqual(packing_slots(100, 100, 10, 42), 0)

    def test_pending_outputs_not_confused_with_unused_cushion(self):
        self.assertEqual(packing_slots(170, 140, 10, 42), 0)
        self.assertEqual(packing_slots(170, 0, 10, 42), 14)

    def test_recovery_requires_exact_identity_and_coverage(self):
        report = dict(complete=True, sha256='binary', baseline_sha256='checkpoint',
                      attempted=2, assigned_functions=['a', 'b'])
        validate_worker(report, ['b', 'a'], 'binary', 'checkpoint')
        for changed in [dict(complete=False), dict(sha256='other'), dict(baseline_sha256='other'),
                        dict(attempted=1), dict(assigned_functions=['a', 'a']),
                        dict(assigned_functions=['a', 'b', 'b'])]:
            with self.assertRaises(RuntimeError):
                validate_worker(dict(report, **changed), ['a', 'b'], 'binary', 'checkpoint')


if __name__ == '__main__':
    unittest.main()
