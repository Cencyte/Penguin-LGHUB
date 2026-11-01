import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import install_storage as storage
import installation_state as ownership
import install_transaction as transaction
import windows_media_acquire as media
from installer_ui import Presentation

ROOT = Path(__file__).resolve().parents[2]


class StorageTests(unittest.TestCase):
    def test_filesystem_probe_cleans_up_and_checks_nearest_existing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)
            result=storage.check_filesystem(parent/'not-created'/'installation')
            self.assertEqual(result['checked_parent'],str(parent))
            self.assertEqual(list(parent.iterdir()),[])

    def test_failed_dirfd_symlink_reports_storage_error_before_downloads(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(storage.os,'symlink',side_effect=FileNotFoundError('fixture NTFS3 failure')):
            with self.assertRaisesRegex(ValueError,'--directory'):
                storage.check_filesystem(Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name);self.root = self.base / 'install'
        self.ui = Presentation(self.base / 'logs/install.log', stream=io.StringIO())
        self.addCleanup(self.ui.close)

    def test_offline_budget_and_actual_low_space_boundary(self):
        plan = storage.capacity_plan(ROOT)
        self.assertEqual(plan['required_free_bytes'], 31 * storage.GIB)
        self.assertEqual(max(plan['phase_bytes'], key=plan['phase_bytes'].get), 'windows')
        pending = plan['required_free_bytes'] - media.FLOOR
        with patch.object(media.shutil, 'disk_usage') as disk:
            disk.return_value.free = 31 * storage.GIB
            media.space_check(self.base, pending)
            disk.return_value.free -= 1
            with self.assertRaisesRegex(RuntimeError, '2 GiB'):media.space_check(self.base, pending)
            # Later helpers retain two GiB, not the old twenty-GiB floor.
            disk.return_value.free = 7 * storage.GIB
            media.space_check(self.base, 5 * storage.GIB)

    def test_changed_inputs_require_budget_review(self):
        source = self.base / 'source';(source / 'packaging').mkdir(parents=True)
        plan = storage.capacity_plan(ROOT)
        for name in ['packaging/install-storage.json', *plan['recipe_locks']]:
            shutil.copyfile(ROOT / name, source / name)
        (source / 'packaging/windows-acquisition.lock.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Pinned inputs changed'):storage.capacity_plan(source)

    def preparing(self, after):
        with ownership.managed_directory(self.root, create=True, source='fixture', version='test') as (_, state):
            state.update(transaction='preparing', completed=[after]);ownership.save(self.root, state)

    def test_disposal_keeps_runtime_required_vendor_cache_reports_and_external_links(self):
        self.preparing('ui')
        outside = self.base / 'documents';outside.mkdir();(outside / 'keep').write_text('personal')
        runtime = self.root / 'work/built-wine';runtime.mkdir(parents=True)
        (runtime / 'wine').write_text('runtime')
        vendor = self.root / 'desktop/prefix/drive_c/ProgramData/LGHUB/cache';vendor.mkdir(parents=True)
        (vendor / 'depot').write_text('required archive')
        for relative in storage.PRUNE_AFTER['ui']:
            path = self.root / relative;path.mkdir()
            (path / 'verification.json').write_text('{"passed":true}')
            (path / 'prefix').mkdir();(path / 'prefix/Documents').symlink_to(outside)
            (path / 'prefix').chmod(0o500)
        storage.prune_verified(self.root, 'ui', self.ui)
        self.assertEqual((runtime / 'wine').read_text(), 'runtime')
        self.assertTrue((vendor / 'depot').exists());self.assertTrue((outside / 'keep').exists())
        for relative in storage.PRUNE_AFTER['ui']:
            self.assertFalse((self.root / relative).exists())
            self.assertEqual(json.loads((self.root / 'provenance' / relative / 'verification.json').read_text()), {'passed':True})

    def test_cleanup_cannot_target_committed_unverified_symlinked_or_mounted_paths(self):
        self.preparing('wine')
        outside = self.base / 'outside';outside.mkdir();(outside / 'keep').write_text('safe')
        (self.root / 'work').mkdir()
        (self.root / 'work/source-build').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):storage.prune_verified(self.root, 'wine', self.ui)
        self.assertTrue((outside / 'keep').exists())
        (self.root / 'work/source-build').unlink();(self.root / 'work/source-build').mkdir()
        import uninstall
        with patch.object(uninstall, 'mounted_paths', return_value=[self.root / 'work/source-build']):
            with self.assertRaisesRegex(RuntimeError, 'Unmount'):storage.prune_verified(self.root, 'wine', self.ui)
        state = ownership.load(self.root);state['completed'] = [];ownership.save(self.root, state)
        with self.assertRaises(ValueError):storage.prune_verified(self.root, 'wine', self.ui)
        state.update(completed=['wine'], installed=True);ownership.save(self.root, state)
        with self.assertRaises(ValueError):storage.prune_verified(self.root, 'wine', self.ui)

    def test_disposal_failure_rolls_back_even_after_earlier_disposal(self):
        import uninstall
        original = storage.shutil.rmtree
        def remove(path, *args, **kwargs):
            if path == self.root / 'work/windows-control' and not (self.root / '.uninstalling').exists():
                raise OSError('injected cleanup failure')
            return original(path, *args, **kwargs)
        remove.avoids_symlink_attacks = True
        def operation(root, state):
            state.update(completed=['windows']);ownership.save(root, state)
            for relative in storage.PRUNE_AFTER['windows']:
                (root / relative).mkdir(parents=True)
                (root / relative / 'temporary').write_text('fixture')
            storage.prune_verified(root, 'windows', self.ui)
        with patch.object(transaction, 'synchronize'), patch.object(storage.shutil, 'rmtree', side_effect=remove) as mocked:
            mocked.avoids_symlink_attacks = True
            with self.assertRaisesRegex(OSError, 'injected cleanup failure'):
                transaction.execute(self.root, 'fixture', 'test', self.ui, operation)
        self.assertFalse(self.root.exists())
        self.assertTrue(list((self.base / 'logs').glob('failed-*/installation.json')))


if __name__ == '__main__':unittest.main()
