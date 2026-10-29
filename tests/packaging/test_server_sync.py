import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import server_sync as m
import linux_ui_probe


class ServerSyncTests(unittest.TestCase):
    def test_no_device_keeps_original_boundary(self):
        command = ['/bwrap', '--dev-bind', '/dev', '/dev', '--', 'true']
        with patch.object(m.Path, 'exists', return_value=False):
            actual, fd = m.boundary_command(command)
        self.assertEqual(actual, command)
        self.assertIsNone(fd)

    def test_readable_device_is_rejected_and_missing_or_denied_is_allowed(self):
        with patch.object(m.os, 'open', return_value=123), patch.object(m.os, 'close') as close:
            with self.assertRaisesRegex(RuntimeError, 'NTSYNC is accessible'):
                m.verify_server_sync()
            close.assert_called_once_with(123)
        for code in (errno.ENOENT, errno.EACCES, errno.EPERM):
            with patch.object(m.os, 'open', side_effect=OSError(code, 'test')):
                m.verify_server_sync()
        with patch.object(m.os, 'open', side_effect=OSError(errno.EMFILE, 'test')):
            with self.assertRaises(OSError):
                m.verify_server_sync()

    def test_ui_gate_challenges_boundary_with_host_ntsync_only(self):
        with patch.object(linux_ui_probe.linux, 'isolated_command',
                          return_value=['bwrap', '--', 'payload']) as isolated:
            for present in (True, False):
                isolated.return_value = ['bwrap', '--', 'payload']
                with patch.object(m.Path, 'exists', return_value=present):
                    command = linux_ui_probe.probe_command(Path('/linux'), Path('/wine'),
                                                          Path('/tools'), Path('/output'))
                self.assertEqual('/dev/ntsync' in command, present)
                self.assertEqual(command[-2:], ['--', 'payload'])
                if present:
                    self.assertEqual(command[1:4], ['--dev-bind', '/dev/ntsync', '/dev/ntsync'])

    @unittest.skipUnless(shutil.which('bwrap') and os.geteuid() != 0, 'needs unprivileged bwrap')
    def test_real_boundary_masks_only_synthetic_device_and_preserves_stdin(self):
        # A fresh /dev prevents creating or altering any host device. A stand-in
        # character device exercises the same mount/open policy without NTSYNC.
        bwrap = shutil.which('bwrap')
        code = ('import os,stat,sys; '
                'sys.path.insert(0,sys.argv[1]); import server_sync; '
                'server_sync.verify_server_sync(); '
                'assert stat.S_ISREG(os.stat("/dev/ntsync").st_mode); '
                'assert os.stat("/dev/ntsync").st_mode & 0o777 == 0; '
                'assert stat.S_ISCHR(os.stat("/dev/null").st_mode); '
                'assert sys.stdin.read() == "preserved input"')
        command = [bwrap, '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev',
                   '--ro-bind', '/dev/null', '/dev/ntsync', '--', sys.executable,
                   str(Path(m.__file__).resolve()), bwrap,
                   '--bind', '/', '/', '--dev-bind', '/dev', '/dev', '--',
                   sys.executable, '-c', code, str(Path(m.__file__).resolve().parent)]
        result = subprocess.run(command, input='preserved input', text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
