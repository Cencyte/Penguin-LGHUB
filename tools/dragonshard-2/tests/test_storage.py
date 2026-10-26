"""Migration invariants: verified bytes before deletion and stable snapshot identity."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dragonshard import source_state
from relocate_campaign import relocate
from preallocate_scratch import reserve_file


class StorageTests(unittest.TestCase):
    def test_preallocation_preserves_live_bytes_and_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache'
            path.write_bytes(b'live database')
            with patch('preallocate_scratch.GIB', 65536):
                reserve_file(path, 0)
            self.assertEqual(path.read_bytes(), b'live database')
            with path.open('ab') as stream:
                stream.write(b' continued')
            self.assertEqual(path.read_bytes(), b'live database continued')

    def test_preallocation_respects_disk_gate_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache'; path.write_bytes(b'only copy')
            with patch('preallocate_scratch.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100, 99, 1)), patch('preallocate_scratch._fallocate') as allocate:
                self.assertEqual(reserve_file(path, 20), 0)
                allocate.assert_not_called()
            link = path.with_suffix('.link'); link.symlink_to(path)
            with self.assertRaises(OSError):
                reserve_file(link, 0)
            self.assertEqual(path.read_bytes(), b'only copy')

    def test_copy_preserves_bytes_links_and_live_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            (source / 'saved').write_bytes(b'full evidence')
            (source / 'link').symlink_to('saved')
            (source / 'live').mkdir(); (source / 'live/db').write_bytes(b'old')
            with (source / 'live/db').open('ab') as live:
                with patch('relocate_campaign.shutil.disk_usage', return_value=shutil._ntuple_diskusage(10**12, 0, 10**12)):
                    relocate(source, root / 'destination', root / 'held', [Path('live')], [])
                live.write(b' continued'); live.flush()
            self.assertTrue(source.is_symlink())
            self.assertEqual((source / 'saved').read_bytes(), b'full evidence')
            self.assertEqual((source / 'link').read_bytes(), b'full evidence')
            self.assertEqual((source / 'live/db').read_bytes(), b'old continued')

    def test_bad_checksum_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            (source / 'saved').write_bytes(b'only good copy')
            with patch('relocate_campaign.shutil.disk_usage', return_value=shutil._ntuple_diskusage(10**12, 0, 10**12)), patch('relocate_campaign.checksum', return_value='bad'):
                with self.assertRaisesRegex(RuntimeError, 'checksum'):
                    relocate(source, root / 'destination', root / 'held', [], [])
            self.assertFalse((source / 'saved').is_symlink())
            self.assertEqual((source / 'saved').read_bytes(), b'only good copy')

    def test_timestamp_bridge_expires_when_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); program = root / 'Test.gpr'; program.write_bytes(b'unchanged')
            (root / 'Test.rep').mkdir()
            info = program.stat(); original = info.st_mtime_ns + 73
            marker = {'Test.gpr': dict(size=info.st_size, original_mtime_ns=original, copied_mtime_ns=info.st_mtime_ns)}
            (root / '.dragonshard-source-times.json').write_text(json.dumps(marker))
            config = dict(project_dir=str(root), project_name='Test')
            self.assertEqual(source_state(config)[0][2], original)
            os.utime(program, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
            self.assertNotEqual(source_state(config)[0][2], original)


if __name__ == '__main__': unittest.main()
