import contextlib
import fcntl
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import desktop_runtime
import host_policy
import host_preflight
import install
import install_device_rules as rules
import installation_state as ownership
import uninstall


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'installation'
        self.ui = MagicMock()

    def create(self, desktop=False):
        with ownership.managed_directory(self.root, create=True, source='fixture', version='test') as (_, state):
            self.state = state
        (self.root / 'work').mkdir()
        (self.root / 'work/download').write_bytes(b'fixture')
        if desktop:
            (self.root / 'desktop').mkdir()
        return self.root

    def entry(self):
        self.create(desktop=True)
        desktop = self.base / 'Desktop';desktop.mkdir()
        apps = self.base / 'applications'
        entry = desktop / 'Penguin-LGHUB.desktop'
        desktop_runtime.install_entry(self.root / 'desktop/desktop.json', entry, applications=apps)
        return entry, apps / entry.name

    def test_remove_full_install_and_launchers_preserves_symlink_targets(self):
        entry, menu = self.entry()
        outside = self.base / 'personal';outside.mkdir()
        (outside / 'save').write_text('keep')
        (self.root / 'desktop/prefix').mkdir()
        (self.root / 'desktop/prefix/user-documents').symlink_to(outside)
        uninstall.remove(self.root, self.ui)
        self.assertFalse(self.root.exists());self.assertFalse(entry.exists());self.assertFalse(menu.exists())
        self.assertFalse(menu.parent.exists())
        self.assertEqual((outside / 'save').read_text(), 'keep')
        self.ui.run.assert_not_called()

    def test_partial_install_removes_only_its_rule(self):
        self.create()
        self.state['attempts']['rules'] = 1
        ownership.save(self.root, self.state)
        uninstall.remove(self.root, self.ui)
        command = self.ui.run.call_args.args[0]
        self.assertEqual(command[0], 'sudo')
        self.assertIn(self.state['installation_id'], command);self.assertIn('--remove', command)
        self.assertFalse(self.root.exists())

    def test_profile_payload_and_stale_mailbox_removed_after_session_stop(self):
        self.create(desktop=True)
        files = ('source/tools/wine/profile_kwin.py', 'source/tools/wine/profile_dbus.py',
                 'source/tools/wine/native_profiles.py',
                 'desktop/prefix/drive_c/Program Files/LGHUB/mfsensorgroup.dll',
                 'desktop/logs/profile-session-test/kwin-focus.js',
                 'desktop/logs/profile-session-test/focus.bin')
        for name in files:
            path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
        outside = self.base/'kwinrc'; outside.write_text('unrelated desktop settings')
        def stopped(*args):
            self.assertTrue(all((self.root/name).is_file() for name in files))
        with patch.object(desktop_runtime.desktop, 'stop', side_effect=stopped) as stop:
            uninstall.remove(self.root, self.ui)
        stop.assert_called_once()
        self.assertFalse(self.root.exists())
        self.assertEqual(outside.read_text(), 'unrelated desktop settings')

    def test_changed_and_symlinked_launchers_block_before_removal(self):
        entry, _ = self.entry()
        entry.write_text('customized')
        with self.assertRaisesRegex(RuntimeError, 'Launcher has changed'):
            uninstall.remove(self.root, self.ui)
        self.assertTrue(self.root.exists());self.ui.run.assert_not_called()
        entry.unlink();entry.symlink_to(self.base / 'unrelated')
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            uninstall.remove(self.root, self.ui)

    def test_existing_external_file_is_never_adopted(self):
        self.create(desktop=True)
        entry = self.base / 'Penguin-LGHUB.desktop';entry.write_text('previous')
        ownership.record_launchers(self.root / 'desktop/desktop.json', [entry], b'previous')
        self.assertEqual(ownership.load(self.root)['launchers'], {})
        uninstall.remove(self.root, self.ui)
        self.assertEqual(entry.read_text(), 'previous')

    def test_directory_must_be_dedicated_and_receipt_must_match_location(self):
        self.root.mkdir();(self.root / 'precious').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'empty dedicated'):
            with ownership.managed_directory(self.root, create=True):pass
        with self.assertRaises(ValueError):uninstall.remove(self.root, self.ui)
        (self.root / 'precious').unlink();self.create()
        moved = self.base / 'moved';self.root.rename(moved)
        with self.assertRaisesRegex(ValueError, 'matching ownership'):
            uninstall.remove(moved, self.ui)
        self.assertTrue((moved / 'work/download').exists())

    def test_mounts_and_top_level_symlinks_block_removal(self):
        self.create()
        with patch.object(uninstall, 'mounted_paths', return_value=[self.root / 'work/mount']):
            with self.assertRaisesRegex(RuntimeError, 'Unmount'):
                uninstall.remove(self.root, self.ui)
        (self.root / 'desktop').symlink_to(self.base)
        with self.assertRaisesRegex(RuntimeError, 'top-level'):
            uninstall.remove(self.root, self.ui)
        self.assertTrue(self.root.exists())

    def test_install_lock_blocks_second_operation(self):
        self.create()
        with ownership.managed_directory(self.root):
            with self.assertRaisesRegex(RuntimeError, 'Another setup'):
                uninstall.remove(self.root, self.ui)
        self.assertTrue(self.root.exists())

    def test_active_launcher_lock_blocks_deletion(self):
        self.create(desktop=True)
        with (self.root / 'desktop/launch.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'still active'):
                with uninstall.stopped_session(self.root, self.ui, timeout=0):pass
        self.assertTrue(self.root.exists())

    def test_unowned_prefix_processes_block_without_killing(self):
        self.create(desktop=True)
        with patch.object(desktop_runtime.desktop, 'prefix_processes', return_value=[(123, 'wine')]), \
             patch.object(desktop_runtime.desktop, 'stop') as stop:
            with self.assertRaisesRegex(RuntimeError, 'Processes still use'):
                uninstall.remove(self.root, self.ui)
            stop.assert_called_once_with(self.root / 'desktop/desktop.json', self.root / 'desktop/session.json')
        self.assertTrue(self.root.exists());self.assertFalse((self.root / '.uninstalling').exists())

    def test_denied_sudo_leaves_payload_and_launcher_intact(self):
        entry, _ = self.entry()
        state = ownership.load(self.root);state['attempts']['rules'] = 1;ownership.save(self.root, state)
        self.ui.run.side_effect = RuntimeError('sudo refused')
        with self.assertRaisesRegex(RuntimeError, 'sudo refused'):
            uninstall.remove(self.root, self.ui)
        self.assertTrue(entry.exists());self.assertTrue(self.root.exists())

    def test_interrupted_payload_removal_keeps_receipt_for_retry(self):
        self.create()
        with patch.object(uninstall.shutil, 'rmtree', side_effect=PermissionError('fixture failure')):
            with self.assertRaises(PermissionError):uninstall.remove(self.root, self.ui)
        self.assertEqual(ownership.load(self.root)['installation_id'], self.state['installation_id'])
        uninstall.remove(self.root, self.ui)
        self.assertFalse(self.root.exists())

    def test_rule_removal_rejects_symlinks_and_other_user_ids(self):
        directory = self.base / 'rules';directory.mkdir()
        target = directory / ('70-penguin-lghub-1000-' + 'a' * 32 + '.rules')
        outside = self.base / 'outside-rule';outside.write_bytes(rules.RULE)
        target.symlink_to(outside)
        with patch.object(rules.os, 'geteuid', return_value=0), patch.dict(os.environ, {'SUDO_UID': '1000'}):
            with self.assertRaisesRegex(RuntimeError, 'symlink'):
                rules.install('a' * 32, remove=True, directory=directory)
            self.assertTrue(outside.exists())
        with patch.dict(os.environ, {'SUDO_UID': '0'}):
            with self.assertRaisesRegex(ValueError, 'desktop user'):
                rules.scoped_target('a' * 32, directory)

    def test_uninstall_needs_no_graphical_session_or_host_preflight(self):
        self.create()
        with patch.object(install.host_preflight, 'inventory', side_effect=AssertionError('must not probe')), \
             patch.object(install.os, 'geteuid', return_value=1000), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(install.main(['--uninstall', '--directory', str(self.root)]), 0)
            self.assertEqual(install.main(['--uninstall', '--directory', str(self.root)]), 0)
        self.assertEqual(output.getvalue().count('\n\n🎉 Uninstall complete! 🎉\n'),1)
        self.assertIn('Nothing was removed.',output.getvalue())

    def test_absent_target_preserves_other_installations_without_success_banner(self):
        legacy = self.base / 'older-lab';legacy.mkdir()
        settings = legacy / 'settings.db';settings.write_bytes(b'personal settings')
        with patch.object(uninstall,'remove',side_effect=AssertionError('must not remove')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(uninstall.run(self.root),0)
        self.assertIn('No managed installation exists at '+str(self.root),output.getvalue())
        self.assertIn('Nothing was removed.',output.getvalue())
        self.assertIn('older lab setups are unchanged',output.getvalue())
        self.assertNotIn('Uninstall complete',output.getvalue())
        self.assertNotIn('🎉',output.getvalue())
        self.assertEqual(settings.read_bytes(),b'personal settings')
        self.assertFalse(self.root.exists())

    def test_failed_uninstall_never_displays_completion(self):
        self.create()
        with patch.dict(os.environ,{'XDG_STATE_HOME':str(self.base/'state')}), \
             patch.object(uninstall,'remove',side_effect=RuntimeError('fixture removal failure')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(uninstall.run(self.root),1)
        self.assertIn('fixture removal failure',output.getvalue())
        self.assertNotIn('🎉',output.getvalue())
        self.assertTrue(self.root.exists())

    def test_rules_are_scoped_and_changed_rules_are_preserved(self):
        directory = self.base / 'rules'
        with patch.object(rules.os, 'geteuid', return_value=0), \
             patch.dict(os.environ, {'SUDO_UID': '1000'}), \
             patch.object(rules.shutil, 'which', return_value=None), \
             patch.object(rules.subprocess, 'run'):
            for ident in ('a' * 32, 'b' * 32):rules.install(ident, directory=directory)
            first = rules.scoped_target('a' * 32, directory)
            second = rules.scoped_target('b' * 32, directory)
            legacy = directory / '70-penguin-lghub.rules';legacy.write_bytes(rules.RULE)
            rules.install('a' * 32, remove=True, directory=directory)
            self.assertFalse(first.exists());self.assertTrue(second.exists());self.assertTrue(legacy.exists())
            second.write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                rules.install('b' * 32, remove=True, directory=directory)
            self.assertTrue(second.exists())
            with self.assertRaises(ValueError):rules.install('../escape', directory=directory)

    def test_failed_launcher_or_rule_write_removes_the_partial_file(self):
        entry=self.base/'Penguin-LGHUB.desktop'
        with patch.object(desktop_runtime.os,'fsync',side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):desktop_runtime.write_entry(entry,b'partial',0o755)
        self.assertFalse(entry.exists())
        directory=self.base/'rules'
        with patch.object(rules.os,'geteuid',return_value=0),patch.dict(os.environ,{'SUDO_UID':'1000'}), \
             patch.object(rules.os,'fsync',side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):rules.install('a'*32,directory=directory)
        self.assertEqual(list(directory.iterdir()),[])

    def test_package_selection_uses_os_identity_not_command_priority(self):
        for distro, manager in [('manjaro', 'pacman'), ('cachyos', 'pacman'), ('bazzite', 'rpm')]:
            with self.subTest(distro=distro), \
                 patch.object(host_preflight, 'os_identity', return_value={'ID': distro}), \
                 patch.object(host_preflight.shutil, 'which', side_effect=lambda n: '/bin/' + n), \
                 patch.object(host_preflight, 'capture', return_value={'status': 0, 'output': ''}) as capture:
                host_preflight.inventory({'XDG_SESSION_TYPE': 'x11'}, self.base)
                package_commands = [c.args[0][0] for c in capture.call_args_list if c.args[0][0] in ('rpm', 'pacman')]
                self.assertEqual(package_commands, [manager])
        self.assertEqual(host_policy.package_query({'ID': 'unsupported'}), [])


if __name__ == '__main__':unittest.main()
