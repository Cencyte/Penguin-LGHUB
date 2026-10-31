import os
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/wine'))
import native_profiles as m


class NativeProfilesTests(unittest.TestCase):
    def test_host_and_container_executable_names(self):
        self.assertEqual(m.wine_path('/usr/bin/kate'), r'Z:\usr\bin\kate')
        self.assertEqual(m.wine_path('/usr/bin/kate', '/run/host'), r'Z:\run\host\usr\bin\kate')
        self.assertEqual(m.wine_path('/home/test/a game', '/run/host'), r'Z:\home\test\a game')
        self.assertEqual(m.wine_path('/mnt/Games/app', '/run/host'), r'Z:\mnt\Games\app')
        for path in ['kate', '/usr/bin/evil\x00path', '/usr/bin/back\\slash']:
            with self.assertRaises(ValueError):
                m.wine_path(path)

    def test_process_identity_is_owned_and_stable(self):
        path, start = m.process_identity(os.getpid())
        self.assertTrue(path.startswith('/'))
        self.assertTrue(start.isdecimal())
        with patch.object(m.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaisesRegex(ValueError, 'different owner'):
                m.process_identity(os.getpid())

    def test_deleted_and_reused_process_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); p = root / '17'; p.mkdir()
            (p / 'exe').symlink_to('/bin/old (deleted)')
            (p / 'stat').write_text('17 (process) ' + ' '.join(['0'] * 20))
            (p / 'cmdline').write_bytes(b'/bin/old\0')
            with self.assertRaisesRegex(ValueError, 'changed'):
                m.process_identity(17, root)
            (p / 'exe').unlink(); (p / 'exe').symlink_to('/bin/new')
            with patch.object(Path, 'read_text', side_effect=['17 (x) '+ '0 '*19+'1', '17 (x) '+'0 '*19+'2']):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    m.process_identity(17, root)

    def test_agent_spawned_windows_tray_name_is_preserved_for_suppression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); p = root / '17'; p.mkdir()
            (p / 'exe').symlink_to('/usr/lib/wine/wine64')
            (p / 'stat').write_text('17 (process) ' + ' '.join(['0'] * 20))
            (p / 'cmdline').write_bytes(b'C:\\Program Files\\LGHUB\\system_tray\\lghub_system_tray.exe\0')
            executable, _ = m.process_identity(17, root)
            self.assertIn(Path(executable).name, m.OWN)

    def test_mailbox_private_atomic_heartbeat_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = m.Mailbox(Path(tmp))
            try:
                self.assertEqual(stat.S_IMODE(box.directory.stat().st_mode), 0o700)
                box.write(r'Z:\usr\bin\kate')
                first = box.path.read_bytes(); header = m.HEADER.unpack_from(first)
                self.assertEqual(stat.S_IMODE(box.path.stat().st_mode), 0o600)
                self.assertEqual(header[0], b'PGFOCUS1')
                self.assertEqual(header[1].decode(), box.token)
                self.assertEqual(header[4], 0x80000001)
                box.write(r'Z:\usr\bin\kate')
                self.assertEqual(box.sequence, 1)  # Heartbeat is not a new focus event.
                box.write(m.DESKTOP); self.assertEqual(box.sequence, 2)
                box.write(r'Z:\usr\bin\kate'); self.assertEqual(box.sequence, 3)
                box.close()
                self.assertEqual(m.HEADER.unpack_from(box.path.read_bytes())[5], 1)
            finally:
                box.cleanup()
            self.assertFalse(box.directory.exists())

    def test_wrong_acknowledgement_token_and_symlink_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); box = m.Mailbox(root)
            try:
                box.path.with_suffix('.bin.status').write_text('bad-token 9 accepted\n')
                self.assertIsNone(box.status())
                outside = root / 'outside'; outside.write_text('preserve')
                box.path.with_suffix('.new').symlink_to(outside)
                with self.assertRaises(FileExistsError):
                    box.write(m.DESKTOP)
                self.assertEqual(outside.read_text(), 'preserve')
            finally:
                box.cleanup()

    def test_wayland_selects_kwin_only_for_verified_adapter_and_host(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'DISPLAY': ':1', 'XDG_SESSION_TYPE': 'wayland'}, clear=True):
            root = Path(tmp); (root/'logs').mkdir()
            app = root/'prefix/drive_c/Program Files/LGHUB'; app.mkdir(parents=True)
            (app/'lghub_agent.exe').write_bytes(b'fixture')
            (app/'mfsensorgroup.dll').write_bytes(m.EXPORT)
            config = {'native_profiles': 1, 'kind': 'penguin-desktop-runtime'}
            with patch.object(m, 'AGENT_SHA256', hashlib.sha256(b'fixture').hexdigest()):
                with self.assertRaisesRegex(RuntimeError, 'host-side'):
                    m.prepare(config, root)
                observer = m.prepare(config, root, host=True)
                self.assertTrue(observer.wayland)
                self.assertEqual(observer.host_root, '/run/host')
                observer.mailbox.cleanup()
        with patch.dict(os.environ, {'DISPLAY': ':0', 'XDG_SESSION_TYPE': 'x11'}, clear=True):
            self.assertIsNone(m.prepare({}, Path('/nonexistent')))

    def test_host_child_readiness_shutdown_and_mailbox_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'logs').mkdir()
            observer = MagicMock()
            observer.mailbox = m.Mailbox(root/'logs')
            observer.thread = None; observer.closed = False
            def start(): observer.thread = object()
            def close():
                observer.closed = True
                observer.mailbox.path.with_suffix('.bin.status').write_text(observer.mailbox.token+' 1 stopped\n')
            observer.start.side_effect = start; observer.close.side_effect = close
            with patch.object(m, 'prepare', return_value=observer):
                with m.HostSession({}, root) as host, patch.dict(os.environ, host.environment()):
                    child = m.RemoteObserver(root)
                    host.poll(); observer.start.assert_not_called()
                    child.start(); host.poll(); observer.start.assert_called_once()
                    child.check()
                    child.mailbox.cleanup()
                    self.assertTrue(observer.mailbox.directory.exists())
                    child.request('stop'); host.poll(); child.close()
                    self.assertTrue(observer.closed)
                self.assertFalse(observer.mailbox.directory.exists())

    def test_child_rejects_stale_host_and_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'logs').mkdir()
            box = m.Mailbox(root/'logs')
            with patch.dict(os.environ, dict(box.environment(), PENGUIN_GHUB_HOST_PROFILE_DIR=str(box.directory))):
                child = m.RemoteObserver(root)
                (box.directory/'host.json').write_text(json.dumps({'token':box.token,'timestamp':0}))
                with self.assertRaisesRegex(RuntimeError, 'stopped responding'):
                    child.check()
                with self.assertRaisesRegex(RuntimeError, 'Invalid host'):
                    m.RemoteObserver(root/'unrelated')
            box.cleanup()

    def test_missing_and_unsupported_adapter_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            observer = m.ProfileObserver(Path(tmp))
            try:
                observer.ack_deadline = 0
                with self.assertRaisesRegex(RuntimeError, 'acknowledge'):
                    observer.check()
                observer.mailbox.path.with_suffix('.bin.status').write_text(observer.mailbox.token+' 0 unsupported-agent\n')
                with self.assertRaisesRegex(RuntimeError, 'pinned'):
                    observer.check()
            finally:
                observer.mailbox.cleanup()


if __name__ == '__main__':
    unittest.main()
