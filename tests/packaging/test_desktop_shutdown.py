from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/wine'))
import tested_desktop_launch as m


class ShutdownTests(unittest.TestCase):
    def test_does_not_start_frontend_when_already_closed(self):
        with patch.object(m, 'prefix_processes', return_value=[(1, 'lghub_agent.exe')]), \
                patch.object(m.subprocess, 'run') as run:
            self.assertTrue(m.stop_frontend(Path('/owned'), Path('/owned/app')))
            run.assert_not_called()

    def test_waits_for_frontend_exit_without_killing_backend(self):
        live = [(1, r'C:\Program Files\LGHUB\lghub.exe'), (2, 'lghub_agent.exe')]
        with patch.object(m, 'prefix_processes', side_effect=[live, live, live[1:], live[1:]]), \
                patch.object(m.subprocess, 'run') as run, patch.object(m.time, 'sleep') as sleep:
            self.assertTrue(m.stop_frontend(Path('/owned'), Path('/owned/app')))
            run.assert_called_once_with(['wine', '/owned/app/lghub.exe', '--killall'],
                                        cwd=Path('/owned/app'), timeout=5, check=False)
            sleep.assert_called_once()

    def test_hung_request_returns_to_session_cleanup(self):
        with patch.object(m, 'prefix_processes', return_value=[(1, 'lghub.exe')]), \
                patch.object(m.subprocess, 'run', side_effect=subprocess.TimeoutExpired('wine', 5)):
            self.assertFalse(m.stop_frontend(Path('/owned'), Path('/owned/app')))

    def test_unresponsive_existing_frontend_has_bounded_wait(self):
        with patch.object(m, 'prefix_processes', return_value=[(1, 'lghub.exe')]), \
                patch.object(m.subprocess, 'run'), patch.object(m.time, 'sleep'), \
                patch.object(m.time, 'monotonic', side_effect=[0, 0, 9]):
            self.assertFalse(m.stop_frontend(Path('/owned'), Path('/owned/app')))


if __name__ == '__main__':
    unittest.main()
