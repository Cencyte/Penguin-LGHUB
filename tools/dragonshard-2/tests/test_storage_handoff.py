"""A storage handoff may consume only a closed, matching saved checkpoint."""
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from handoff_analysis_storage import release_parent, validate_checkpoint


class HandoffTests(unittest.TestCase):
    def test_only_a_closed_complete_checkpoint_can_be_transferred(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'driver'; project.mkdir()
            report = dict(mode='finish', sha256='binary', complete=True,
                          source_dir=str(project), source_name='Driver')
            (root / 'program.gzf').write_bytes(b'preserved database')
            (root / 'functions.json').write_text('[]')
            (root / 'report.json').write_text(json.dumps(report))
            self.assertEqual(validate_checkpoint(root, 'binary'), report)
            for change in [dict(complete=False), dict(mode='worker'), dict(sha256='other')]:
                (root / 'report.json').write_text(json.dumps(dict(report, **change)))
                with self.assertRaises(RuntimeError): validate_checkpoint(root, 'binary')
            (root / 'report.json').write_text(json.dumps(report))
            lock = project / 'Driver.lock'; lock.touch()
            with self.assertRaisesRegex(RuntimeError, 'locked'): validate_checkpoint(root, 'binary')
            lock.unlink(); (root / 'program.gzf').unlink()
            with self.assertRaisesRegex(RuntimeError, 'missing'): validate_checkpoint(root, 'binary')

    def test_cleanup_releases_only_the_original_live_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'state.json'
            config = dict(source_unit='dragonshard2-original.service', source=str(root / 'source'))
            state = dict(state='waiting_for_closed_checkpoint', original_retired=False,
                         coordinator_pid=123, coordinator_start_tick='original')
            path.write_text(json.dumps(state))
            with patch('handoff_analysis_storage.prop', return_value='123'), \
                 patch('handoff_analysis_storage.coordinator_identity', return_value='original'), \
                 patch('handoff_analysis_storage.os.kill') as kill:
                release_parent(config, path)
                kill.assert_called_once_with(123, signal.SIGCONT)
            for start, retired, main in [('reused', False, '123'), ('original', True, '123'), ('original', False, '999')]:
                path.write_text(json.dumps(dict(state, original_retired=retired)))
                with patch('handoff_analysis_storage.prop', return_value=main), \
                     patch('handoff_analysis_storage.coordinator_identity', return_value=start), \
                     patch('handoff_analysis_storage.os.kill') as kill:
                    release_parent(config, path)
                    kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
