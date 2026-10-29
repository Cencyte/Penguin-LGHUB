import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import desktop_runtime as m
import startup_feedback as feedback


class StartupTests(unittest.TestCase):
    def test_readiness_rejects_stale_partial_and_wrong_session_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'logs').mkdir()
            report = root / 'logs/frontend-ready.json'
            for content in ('{', '[]', '{"token":"previous","ready":true}',
                            '{"token":"current","ready":false}'):
                report.write_text(content)
                self.assertTrue(m.desktop.frontend_startup_pending(root, 'current', 60, 5))
            with self.assertRaises(m.desktop.FrontendStartupTimeout):
                m.desktop.frontend_startup_pending(root, 'current', 60, 60)
            report.write_text(json.dumps({'token': 'current', 'ready': True}))
            self.assertFalse(m.desktop.frontend_startup_pending(root, 'current', 60, 60))

    def run_case(self, statuses, processes=()):
        children = []
        for status in statuses:
            child = MagicMock(); child.__enter__.return_value = child
            child.poll.return_value = status; child.returncode = status
            children.append(child)
        out = io.StringIO()
        with patch.object(m.subprocess, 'Popen', side_effect=children) as popen, \
                patch.object(m, 'container_command', return_value=['owned-runtime']), \
                patch.object(m.desktop, 'prefix_processes', return_value=processes):
            status = m.run_container_session(Path('/owned/desktop.json'), {}, Path('/owned'),
                                             ['owned-worker'], out, MagicMock(completed=True), True)
        return status, popen.call_count, out.getvalue()

    def test_clean_start_and_normal_shutdown_never_retry(self):
        self.assertEqual(self.run_case([0])[:2], (0, 1))

    def test_only_startup_timeout_retries_once(self):
        status, count, log = self.run_case([75, 0])
        self.assertEqual((status, count), (0, 2))
        self.assertIn('retrying', log)
        self.assertEqual(self.run_case([75, 75])[:2], (75, 2))
        self.assertEqual(self.run_case([1])[:2], (1, 1))

    def test_no_retry_over_a_remaining_wine_process(self):
        with self.assertRaisesRegex(RuntimeError, 'did not stop completely'):
            self.run_case([75], [(123, 'lghub_agent.exe')])

    def test_x11_feedback_chunks_escape_identifier_and_finish_once_on_error(self):
        # XClientMessage data is a union containing longs, even for format 8.
        self.assertEqual(feedback.ClientMessage.data.offset % feedback.c.alignment(feedback.c.c_long), 0)
        value = 'machine/"quoted"\\path_TIME12345'
        chunks = feedback.message_chunks(value)
        self.assertTrue(all(len(chunk) <= 20 for chunk in chunks))
        self.assertEqual(b''.join(chunks), b'remove: ID="machine/\\"quoted\\"\\\\path_TIME12345"\0')
        with patch.object(feedback, 'finish') as done:
            with self.assertRaises(ValueError), feedback.Feedback({'DISPLAY': ':9', 'DESKTOP_STARTUP_ID': value}) as f:
                f.complete()
                raise ValueError('failed launch')
            done.assert_called_once_with(':9', value)
        with patch.object(feedback, 'finish') as done:
            with feedback.Feedback({'DISPLAY': ':9', 'XDG_ACTIVATION_TOKEN': 'wayland-token'}):
                pass
            done.assert_not_called()  # Wayland tokens are not X11 notification IDs.


if __name__ == '__main__':
    unittest.main()
