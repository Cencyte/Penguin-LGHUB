import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from campaign import choose_workers, verify_and_clean_clones
from dragonshard import analysis_blockers


class CampaignTests(unittest.TestCase):
    def setUp(self):
        cores = patch('campaign.os.cpu_count', return_value=48)
        cores.start()
        self.addCleanup(cores.stop)

    def test_workers_respect_memory_headroom(self):
        self.assertEqual(choose_workers(62, 33, .09, 6, 32), 32)
        self.assertEqual(choose_workers(20, 33, .09, 6, 32), 6)

    def test_disk_can_limit_parallelism_without_dropping_programs(self):
        self.assertEqual(choose_workers(100, 20, 2, 6, 32), 3)

    def test_no_workers_when_reserve_cannot_be_kept(self):
        for memory, disk in ((8, 33), (100, 13)):
            with self.assertRaises(RuntimeError):
                choose_workers(memory, disk, .09, 6, 32)

    def test_measured_worker_budget_still_preserves_host_reserve(self):
        self.assertEqual(choose_workers(60, 100, .1, 8, 48, 1.25), 40)
        with self.assertRaises(ValueError):
            choose_workers(60, 100, .1, 8, 48, 0)

    def test_clone_cleanup_refuses_unowned_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = Path(temp)
            run = batch / 'run'
            clones = run / 'clones'
            clones.mkdir(parents=True)
            sentinel = clones / 'preserve'
            sentinel.write_text('not ours')
            (run / 'owner.json').write_text(json.dumps({'tool': 'unrelated'}))
            (run / 'manifest.json').write_text(json.dumps({'run_dir': str(run)}))
            with self.assertRaisesRegex(RuntimeError, 'ownership'):
                verify_and_clean_clones(batch)
            self.assertEqual(sentinel.read_text(), 'not ours')


class AnalysisDepthTests(unittest.TestCase):
    def check_report(self, report):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'audit.json'
            path.write_text(json.dumps(report))
            return analysis_blockers({'sha256': 'a' * 64, 'analysis_audit': str(path)})

    def test_missing_depth_is_not_inferred_from_decompiler_success(self):
        self.assertTrue(analysis_blockers({'sha256': 'a' * 64}))

    def test_placeholder_listing_rejected(self):
        self.assertTrue(self.check_report({'sha256': 'a' * 64, 'complete': True,
            'after': {'functions': 1000, 'instruction_count': 0,
                      'functions_without_entry_instruction': 1000}}))

    def test_sparse_partially_analyzed_listing_rejected(self):
        self.assertTrue(self.check_report({'sha256': 'a' * 64, 'complete': True,
            'after': {'functions': 1000, 'instruction_count': 100,
                      'functions_without_entry_instruction': 900}}))

    def test_wrong_binary_or_incomplete_report_rejected(self):
        for sha, complete in [('b' * 64, True), ('a' * 64, False)]:
            self.assertTrue(self.check_report({'sha256': sha, 'complete': complete}))

    def test_real_one_byte_return_instructions_are_not_missing_code(self):
        self.assertEqual(self.check_report({'sha256': 'a' * 64, 'complete': True,
            'after': {'functions': 1000, 'instruction_count': 10000,
                      'one_byte_bodies': 30, 'functions_without_entry_instruction': 0}}), [])

    def test_proven_synthetic_external_thunks_are_not_missing_disassembly(self):
        self.assertEqual(self.check_report({'sha256': 'a' * 64, 'complete': True,
            'after': {'functions': 18, 'instruction_count': 101,
                      'functions_without_entry_instruction': 6,
                      'synthetic_external_thunks_without_instructions': 6}}), [])


if __name__ == '__main__':
    unittest.main()
