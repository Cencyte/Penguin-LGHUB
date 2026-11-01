import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/wine'))
import native_profiles
import profile_support


class ProfileSupportTests(unittest.TestCase):
    def test_x11_checks_libraries_without_opening_windows_or_kwin(self):
        with patch.object(profile_support.ctypes, 'CDLL') as library, \
             patch.object(profile_support, 'command_result') as command:
            result = profile_support.inspect({'XDG_SESSION_TYPE': 'x11'})
        self.assertEqual(result['errors'], [])
        self.assertEqual(library.call_count, 2)
        command.assert_not_called()

    def test_missing_host_library_is_a_blocker(self):
        with patch.object(profile_support.ctypes, 'CDLL', side_effect=OSError('missing')):
            result = profile_support.inspect({'XDG_SESSION_TYPE': 'x11'})
        self.assertEqual(len(result['errors']), 2)

    def test_wayland_checks_only_read_only_capabilities_on_selected_bus(self):
        env = {'XDG_SESSION_TYPE': 'wayland', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/private/bus'}
        with patch.object(profile_support.ctypes, 'CDLL'), \
             patch.object(profile_support, 'command_result', side_effect=[
                 (0, 'Operation Mode: Wayland'),
                 (0, 'org.kde.kwin.Scripting loadScript unloadScript')]) as command:
            result = profile_support.inspect(env)
        self.assertEqual(result['errors'], [])
        for call in command.call_args_list:
            self.assertEqual(call.args[1], env)
            self.assertNotIn('org.kde.kwin.Scripting.loadScript', call.args[0])

    def test_wrong_compositor_or_missing_scripting_blocks(self):
        for replies in [[(0, 'Operation Mode: X11'), (0, 'org.kde.kwin.Scripting loadScript unloadScript')],
                        [(0, 'Operation Mode: Wayland'), (1, 'unavailable')]]:
            with patch.object(profile_support.ctypes, 'CDLL'), \
                 patch.object(profile_support, 'command_result', side_effect=replies):
                self.assertTrue(profile_support.inspect({'XDG_SESSION_TYPE': 'wayland'})['errors'])

    def test_profile_payload_rejects_silent_disabled_or_missing_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); app = root / 'prefix/drive_c/Program Files/LGHUB'
            app.mkdir(parents=True)
            source = root / 'source/tools/wine'; source.mkdir(parents=True)
            for name in profile_support.MODULES: (source/name).touch()
            agent = b'pinned fixture agent'; (app/'lghub_agent.exe').write_bytes(agent)
            adapter = app/'mfsensorgroup.dll'; adapter.write_bytes(native_profiles.EXPORT)
            config = root/'desktop.json'; config.write_text(json.dumps({'native_profiles': 1}))
            with patch.object(native_profiles, 'AGENT_SHA256', hashlib.sha256(agent).hexdigest()):
                self.assertTrue(profile_support.verify_payload(config, root/'source')['native_profiles'])
                config.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'missing or disabled'):
                    profile_support.verify_payload(config, root/'source')
                config.write_text(json.dumps({'native_profiles': 1})); adapter.write_bytes(b'old fallback')
                with self.assertRaisesRegex(ValueError, 'missing or disabled'):
                    profile_support.verify_payload(config, root/'source')
                adapter.write_bytes(native_profiles.EXPORT); (source/'profile_kwin.py').unlink()
                with self.assertRaisesRegex(ValueError, 'Missing packaged'):
                    profile_support.verify_payload(config, root/'source')


if __name__ == '__main__':
    unittest.main()
