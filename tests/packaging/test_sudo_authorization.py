import io
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
from installer_ui import Presentation


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.output = io.StringIO()
        self.ui = Presentation(self.base / 'log', stream=self.output)
        self.addCleanup(self.ui.close)

    def probe(self, status):
        return patch('installer_ui.subprocess.run', return_value=subprocess.CompletedProcess(
            ['sudo', '-n', '-v'], status, '' if status == 0 else 'sudo: a password is required\n'))

    def test_cached_authorization_needs_no_enter(self):
        with self.probe(0) as probe, patch('installer_ui.open') as terminal:
            self.ui.wait_for_sudo()
        terminal.assert_not_called()
        self.assertEqual(probe.call_args.args[0], ['sudo', '-n', '-v'])
        self.assertEqual(probe.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_enter_precedes_helper_and_is_not_logged(self):
        class Terminal(io.StringIO):
            def readline(inner):
                self.assertEqual(self.ui._spinner_suspended, 1)
                self.assertIn('no countdown', self.output.getvalue())
                return super().readline()
        terminal = Terminal('arbitrary-user-input\n')
        with self.probe(1), patch('installer_ui.open', return_value=terminal) as opened, \
                patch.object(self.ui, '_run') as helper:
            self.ui.run(['sudo', '/usr/bin/python3', 'scoped-helper'])
        opened.assert_called_once_with('/dev/tty', 'r')
        helper.assert_called_once()
        self.assertEqual(self.ui._spinner_suspended, 0)
        self.assertNotIn('arbitrary-user-input', (self.base / 'log').read_text())

    def test_cancellation_never_starts_privileged_helper(self):
        terminal = io.StringIO()
        with self.probe(1), patch('installer_ui.open', return_value=terminal), \
                patch.object(terminal, 'readline', side_effect=KeyboardInterrupt), \
                patch.object(self.ui, '_run') as helper:
            with self.assertRaises(KeyboardInterrupt):self.ui.run(['sudo', 'helper'])
        helper.assert_not_called()
        self.assertEqual(self.ui._spinner_suspended, 0)

    def test_no_terminal_or_eof_never_starts_privileged_helper(self):
        for missing in (True, False):
            with self.subTest(missing=missing), self.probe(1), \
                    patch('installer_ui.open', side_effect=OSError('no tty') if missing else None,
                          return_value=io.StringIO()), patch.object(self.ui, '_run') as helper:
                with self.assertRaisesRegex(RuntimeError, 'terminal'):
                    self.ui.run(['sudo', 'helper'])
                helper.assert_not_called()

    def test_non_sudo_command_never_prompts(self):
        with patch.object(self.ui, 'wait_for_sudo') as authorize, patch.object(self.ui, '_run') as helper:
            self.ui.run(['/usr/bin/true'])
        authorize.assert_not_called();helper.assert_called_once()

    def test_fresh_private_pty_waits_before_mock_sudo_starts(self):
        # Own a new PTY just for this automated test. Never touch a desktop
        # terminal or cached device number, and never invoke real sudo.
        fake = self.base / 'sudo'
        fake.write_text('#!' + sys.executable + '\nimport sys\n'
                        'if sys.argv[1:] == ["-n", "-v"]: sys.exit(1)\n'
                        'with open("/dev/tty", "w") as tty:\n'
                        ' print("mock privileged helper started", file=tty, flush=True)\n')
        fake.chmod(0o700)
        script = ('import sys\nfrom pathlib import Path\n'
                  'sys.path.insert(0,' + repr(str(Path(__file__).resolve().parents[2] / 'tools/packaging')) + ')\n'
                  'from installer_ui import Presentation\n'
                  'ui=Presentation(Path(' + repr(str(self.base / 'pty.log')) + '))\n'
                  'try: ui.run(["sudo", "fixture-helper"])\nfinally: ui.close()\n'
                  'print("authorization test complete", flush=True)\n')
        script_path = self.base / 'child.py';script_path.write_text(script)
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, str(script_path)],
                      dict(os.environ, PATH=str(self.base), PYTHONDONTWRITEBYTECODE='1'))
        received = b''
        def read_until(marker):
            nonlocal received
            deadline = time.monotonic() + 8
            while marker not in received and time.monotonic() < deadline:
                if select.select([fd], [], [], .1)[0]:
                    try:data = os.read(fd, 4096)
                    except OSError:break
                    if not data:break
                    received += data
            self.assertIn(marker, received)
        try:
            read_until(b'or Ctrl+C to cancel.\r\n')
            self.assertNotIn(b'mock privileged helper started', received)
            self.assertFalse(select.select([fd], [], [], .2)[0])
            os.write(fd, b'\n')
            read_until(b'mock privileged helper started')
            read_until(b'authorization test complete')
        finally:
            os.close(fd)
            try:os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:pass
            os.waitpid(pid, 0)


if __name__ == '__main__':unittest.main()
