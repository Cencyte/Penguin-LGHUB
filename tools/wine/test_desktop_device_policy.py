#!/usr/bin/env python3
"""Prevent device-isolated replay settings from reaching a Desktop release."""
import unittest
from pathlib import Path
from unittest.mock import patch

from prepare_tested_desktop import desktop_environment, install_entry
from tested_desktop_launch import check, register_frontend_protocol


class DesktopDevicePolicy(unittest.TestCase):
    def test_promote_lab_environment(self):
        original = {'WINEDLLOVERRIDES': 'winebus.sys=d;wineusb.sys=d;ninput=n;'
                    'lghub_software_manager.exe=d;coremessaging=n',
                    'WINE_GHUB_POINTER_MOUSE': '1'}
        clean = desktop_environment(original)
        self.assertEqual(clean['WINEDLLOVERRIDES'],
                         'ninput=n;lghub_software_manager.exe=d;coremessaging=n')
        self.assertEqual(clean['WINE_GHUB_POINTER_MOUSE'], '1')
        self.assertIn('winebus.sys=d', original['WINEDLLOVERRIDES'])
        self.assertEqual(desktop_environment(clean), clean)
        self.assertEqual(desktop_environment({'WINE_GHUB_POINTER_MOUSE': '1'}),
                         {'WINE_GHUB_POINTER_MOUSE': '1'})
        self.assertEqual(desktop_environment({'WINEDLLOVERRIDES': 'ninput=n;'}),
                         {'WINEDLLOVERRIDES': 'ninput=n;'})

    def test_grouped_case_insensitive_exclusions(self):
        self.assertEqual(desktop_environment({'WINEDLLOVERRIDES':
            'WINEBUS.SYS,wineusb,winemenubuilder.exe=;dcomp=n'})['WINEDLLOVERRIDES'],
            'winemenubuilder.exe=;dcomp=n')

    def test_desktop_install_rejects_lab_override_before_writing(self):
        config = {'environment': {'WINEDLLOVERRIDES': 'winebus.sys=d;ninput=n'}}
        with patch('prepare_tested_desktop.check'), \
                patch.object(Path, 'read_text', return_value='{}'), \
                patch('prepare_tested_desktop.json.loads', return_value=config), \
                patch('prepare_tested_desktop.subprocess.check_output') as desktop_lookup:
            with self.assertRaisesRegex(RuntimeError, 'USB/HID'):
                install_entry(Path('/unused'))
            desktop_lookup.assert_not_called()

    def test_protocol_registration_failure_stops_startup(self):
        with patch('tested_desktop_launch.subprocess.run') as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(RuntimeError, 'tray link handler'):
                register_frontend_protocol()
            self.assertEqual(run.call_count, 1)

    def test_host_keyboard_rejects_mixed_or_unpinned_build(self):
        config = {'schema': 1, 'environment': {'WINE_GHUB_HOST_KEYBOARD': '1'},
                  'builds': {'--composition-build-dir': 'builds/a', '--x11-build-dir': 'builds/b'},
                  'sha256': {}}
        with patch.object(Path, 'is_file', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'matched Win32u'):
                check(config, Path('/unused'))
            config['builds']['--x11-build-dir'] = 'builds/a'
            with self.assertRaisesRegex(RuntimeError, 'not pinned'):
                check(config, Path('/unused'))


if __name__ == '__main__':
    unittest.main()
