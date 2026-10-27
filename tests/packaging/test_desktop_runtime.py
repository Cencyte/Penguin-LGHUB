import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import desktop_runtime as m
import linux_probe
from windows_media_acquire import digest_file


class DesktopRuntimeTests(unittest.TestCase):
    def test_wayland_with_xwayland_is_not_mistaken_for_x11(self):
        self.assertTrue(m.session_errors({'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':0'}))
        self.assertFalse(m.session_errors({'XDG_SESSION_TYPE': 'x11', 'DISPLAY': ':12.0'}))
        self.assertTrue(m.session_errors({'XDG_SESSION_TYPE': 'x11', 'DISPLAY': 'localhost:10.0'}))

    def test_session_environment_drops_loader_injection_and_retains_cookie(self):
        source = {'DISPLAY': ':4', 'XAUTHORITY': '/run/user/1000/xauth',
                  'XDG_SESSION_TYPE': 'x11', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus',
                  'LD_PRELOAD': '/evil', 'WINESERVER': '/host', 'WINEPREFIX': '/old',
                  'PRESSURE_VESSEL_RUNTIME': '/other', 'WAYLAND_DISPLAY': 'wayland-0'}
        with patch.dict(os.environ, source):
            env = m.session_environment(source)
        for name in ('DISPLAY', 'XAUTHORITY', 'XDG_SESSION_TYPE', 'DBUS_SESSION_BUS_ADDRESS'):
            self.assertEqual(env[name], source[name])
        for name in ('LD_PRELOAD', 'WINESERVER', 'WINEPREFIX', 'PRESSURE_VESSEL_RUNTIME', 'WAYLAND_DISPLAY'):
            self.assertNotIn(name, env)

    def test_container_shares_supervisor_identity_and_freezes_app_after_output(self):
        config = {'linux_runtime': '/private linux', 'wine': '/private wine', 'test_tools': '/tools'}
        cmd = m.container_command(config, Path('/session'), ['true'])
        self.assertIn('--share-pid', cmd)
        self.assertNotIn('--unshare-pid', cmd)
        self.assertIn('--graphics-provider=/', cmd)
        self.assertIn('--filesystem=/private linux:ro', cmd)
        self.assertIn('--dev-bind', cmd)
        self.assertNotIn('--graphics-provider=', cmd)
        self.assertLess(cmd.index('--filesystem=/session:rw'), cmd.index('--ro-bind'))
        self.assertIn('/session/prefix/drive_c/Program Files/LGHUB', cmd)
        self.assertNotIn('sudo', cmd)

    def test_headless_overlay_preserves_synthetic_device_nodes(self):
        cmd = linux_probe.test_command(Path('/linux'), Path('/tools'), ['true'])
        i = cmd.index('--dev-bind')
        self.assertEqual(cmd[i + 1:i + 3], ['/dev', '/dev'])

    def test_launcher_keeps_paths_with_spaces_and_desktop_field_codes_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'launcher.desktop'
            m.install_entry(Path('/test space/%config.json'), dest)
            text = dest.read_text()
            self.assertIn('"/test space/%%config.json" launch', text)
            self.assertIn('[Desktop Action Stop]', text)
            with self.assertRaises(FileExistsError):
                m.install_entry(Path('/elsewhere'), dest)

    def test_fixed_file_escape_refused_before_runtime_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'session'; root.mkdir()
            outside = Path(tmp) / 'outside'; outside.write_text('payload')
            (root / 'file').symlink_to(outside)
            config = {'schema': 1, 'kind': 'penguin-desktop-runtime',
                      'fixed_files': {'file': digest_file(outside)['sha256']}}
            with self.assertRaisesRegex(ValueError, 'component changed'):
                m.verify(config, root)

    def test_transferred_probe_requires_matching_source_and_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'probe.exe'; exe.write_bytes(b'fixture')
            receipt = {'source_sha256': digest_file(m.ROOT / 'tools/packaging/clean_prefix_probe.cpp')['sha256'],
                       'exe_sha256': digest_file(exe)['sha256']}
            exe.with_suffix('.json').write_text(json.dumps(receipt))
            linux_probe.verify_activation_probe(exe)
            exe.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                linux_probe.verify_activation_probe(exe)


if __name__ == '__main__':
    unittest.main()
