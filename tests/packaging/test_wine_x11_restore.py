import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import desktop_runtime as desktop
import wine_x11_restore
import wine_desktop_init


class RestoreOverlayTests(unittest.TestCase):
    overlay_module = wine_x11_restore
    target_name = "winex11.so"
    config_key = "x11_restore_override"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.install = self.root / 'install'; self.install.mkdir()
        self.module = self.root / 'candidate.so'; self.module.write_bytes(b'new driver')
        self.wine = self.root / 'wine'
        self.target = self.wine / 'usr/lib/wine/x86_64-unix' / self.target_name
        self.target.parent.mkdir(parents=True); self.target.write_bytes(b'base driver')
        self.lock = {'module_sha256': hashlib.sha256(b'new driver').hexdigest(),
                     'base_sha256': hashlib.sha256(b'base driver').hexdigest(),
                     'target': str(self.target.relative_to(self.wine))}
        self.config = self.install / 'desktop.json'
        self.config.write_text(json.dumps({'wine': str(self.wine), 'fixed_files': {},
                                           'linux_runtime': '/linux', 'test_tools': '/tools'}))
        self.original = self.config.read_bytes()
        mocking = patch.object(self.overlay_module, 'recipe', return_value=self.lock)
        mocking.start(); self.addCleanup(mocking.stop)

    def test_staging_and_mount_keep_base_runtime_and_rollback_intact(self):
        result = self.overlay_module.stage(self.config, self.module)
        self.assertEqual(self.target.read_bytes(), b'base driver')
        self.assertEqual(Path(result['previous_config']).read_bytes(), self.original)
        config = json.loads(self.config.read_text())
        source, target = self.overlay_module.overlay(config, self.install)
        command = desktop.container_command(config, self.install, ['true'])
        at = command.index(str(source))
        self.assertEqual(command[at-1:at+2], ['--ro-bind', str(source), str(target)])
        self.assertEqual(source.read_bytes(), b'new driver')
        # Staging twice must retain the first rollback configuration.
        self.overlay_module.stage(self.config, self.module)
        self.assertEqual(Path(result['previous_config']).read_bytes(), self.original)

    def test_wrong_candidate_and_wrong_base_leave_config_unchanged(self):
        self.module.write_bytes(b'unrecognized driver')
        with self.assertRaisesRegex(ValueError, 'Unrecognized'):
            self.overlay_module.stage(self.config, self.module)
        self.module.write_bytes(b'new driver')
        self.target.write_bytes(b'different Wine version')
        with self.assertRaisesRegex(ValueError, 'pinned base'):
            self.overlay_module.stage(self.config, self.module)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_changed_overlay_or_escaped_path_is_rejected_before_launch(self):
        result = self.overlay_module.stage(self.config, self.module)
        config = json.loads(self.config.read_text())
        (self.install / result['overlay']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'overlay changed'):
            desktop.container_command(config, self.install, ['true'])
        config[self.config_key] = '../candidate.so'
        with self.assertRaisesRegex(ValueError, 'Unrecognized'):
            desktop.container_command(config, self.install, ['true'])

    def test_symlink_overlay_directory_cannot_write_outside_installation(self):
        outside = self.root / 'outside'; outside.mkdir()
        (self.install / 'wine-overlays').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.overlay_module.stage(self.config, self.module)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.config.read_bytes(), self.original)


class DesktopInitOverlayTests(RestoreOverlayTests):
    overlay_module = wine_desktop_init
    target_name = "win32u.so"
    config_key = "desktop_init_override"
