"""A saved checkpoint can be exported without claiming final convergence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from packed_export import validate_checkpoint


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.program = dict(sha256='a' * 64)
        self.report = dict(sha256='a' * 64, complete=True, mode='finish')
        self.audit = dict(sha256='a' * 64, complete=True, after=dict(functions=100,
                          instruction_count=1000, functions_without_entry_instruction=0))
        (self.root / 'program.gzf').write_bytes(b'fixture')
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        (self.root / 'report.json').write_text(json.dumps(self.report))
        (self.root / 'depth.json').write_text(json.dumps(self.audit))

    def test_saved_audited_checkpoint_is_exportable_before_fixed_point(self):
        self.assertEqual(validate_checkpoint(self.root, self.program), 100)
        self.assertNotIn('constant_propagation_fixed_point', self.report)

    def test_reject_incomplete_or_different_checkpoint(self):
        for changed in (dict(complete=False), dict(mode='worker'), dict(sha256='b' * 64)):
            with self.subTest(changed=changed):
                (self.root / 'report.json').write_text(json.dumps(dict(self.report, **changed)))
                with self.assertRaises(ValueError):
                    validate_checkpoint(self.root, self.program)

    def test_reject_shallow_or_wrong_binary_audit(self):
        self.audit['after']['functions_without_entry_instruction'] = 2
        self.write()
        with self.assertRaisesRegex(ValueError, 'no entry instruction'):
            validate_checkpoint(self.root, self.program)
        self.audit['sha256'] = 'b' * 64
        self.write()
        with self.assertRaisesRegex(ValueError, 'wrong-binary'):
            validate_checkpoint(self.root, self.program)

    def test_reject_missing_saved_database(self):
        (self.root / 'program.gzf').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            validate_checkpoint(self.root, self.program)
