"""Only the exact saved nonconvergence gate is eligible for automatic remerge."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from resume_convergence import NONCONVERGED, recovery_checkpoint


class RecoveryGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.program = dict(sha256='a' * 64)
        self.write('owner.json', dict(tool='dragonshard-2-parallel-analysis'))
        self.write('status.json', dict(state='blocked_or_failed', error=NONCONVERGED))
        self.write('finish-04/report.json', dict(mode='finish', complete=True, sha256='a' * 64))
        self.write('finish-04/depth.json', dict(sha256='a' * 64, complete=True,
                   after=dict(functions=10, instruction_count=100, functions_without_entry_instruction=0)))
        (self.root / 'finish-04/program.gzf').write_bytes(b'fixture')

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_expected_gate_requires_saved_audited_final_checkpoint(self):
        self.assertEqual(recovery_checkpoint(self.root, self.program), self.root / 'finish-04')
        (self.root / 'finish-04/program.gzf').unlink()
        with self.assertRaises(ValueError):
            recovery_checkpoint(self.root, self.program)

    def test_never_recover_arbitrary_errors_or_active_runs(self):
        for state in [dict(state='remaining_analyzers', error=NONCONVERGED),
                      dict(state='blocked_or_failed', error='Disk reserve reached'),
                      dict(state='complete')]:
            self.write('status.json', state)
            with self.assertRaises(ValueError):
                recovery_checkpoint(self.root, self.program)

    def test_never_adopt_unowned_or_different_binary_results(self):
        with self.assertRaises(ValueError):
            recovery_checkpoint(self.root, dict(sha256='b' * 64))
        self.write('owner.json', dict(tool='unrelated'))
        with self.assertRaises(ValueError):
            recovery_checkpoint(self.root, self.program)


if __name__ == '__main__':
    unittest.main()
