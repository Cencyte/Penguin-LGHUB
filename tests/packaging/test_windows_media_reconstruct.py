import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / 'tools/packaging'
with patch.object(sys, 'path', [str(TOOLS), *sys.path]):
    SPEC = importlib.util.spec_from_file_location('media_reconstruct', TOOLS / 'windows_media_reconstruct.py')
    m = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(m)
    import windows_media_acquire as acquire


class ReconstructionTests(unittest.TestCase):
    def test_raw_resource_bounds_and_hash(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
            root = Path(d)
            source = root / 'package'
            source.write_bytes(b'prefixDATAtrailer')
            spec = {'offset': 6, 'size': 4, 'sha256': hashlib.sha256(b'DATA').hexdigest()}
            m.read_resource(source, root / 'valid', spec)
            self.assertEqual((root / 'valid').read_bytes(), b'DATA')
            with self.assertRaisesRegex(ValueError, 'bounds'):
                m.read_resource(source, root / 'outside', {**spec, 'offset': 100})
            self.assertFalse((root / 'outside').exists())
            with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
                m.read_resource(source, root / 'wrong', {**spec, 'offset': 0})

    def test_cix_wrong_architecture_hash_range_or_duplicate_rejected(self):
        spec = {'path': 'amd64_component\\f\\d2d1.dll', 'offset': 10, 'size': 20, 'sha256': 'a' * 64}
        entry = (f'<File name="{spec["path"]}"><Delta><Source type="RAW" offset="10" length="20">'
                 f'<Hash alg="SHA256" value="{spec["sha256"]}"/></Source></Delta></File>')
        with tempfile.TemporaryDirectory() as d:
            cix = Path(d) / 'index.xml'
            def write(body):
                cix.write_text('<Container xmlns="urn:ContainerIndex"><Files>' + body + '</Files></Container>')
            write(entry)
            self.assertEqual(m.select_forward_deltas(cix, {'d2d1.dll': spec}, 30), {'d2d1.dll': spec})
            for invalid in (entry.replace('amd64_', 'wow64_'), entry.replace('a' * 64, 'b' * 64),
                            entry.replace('offset="10"', 'offset="11"'), entry + entry):
                write(invalid)
                with self.assertRaises(ValueError):
                    m.select_forward_deltas(cix, {'d2d1.dll': spec}, 30)
            write(entry)
            with self.assertRaises(ValueError):
                m.select_forward_deltas(cix, {'d2d1.dll': spec}, 29)

    def test_archive_traversal_and_links_rejected(self):
        for name, kind in [('ms-compress-test/../../escape', tarfile.REGTYPE),
                           ('ms-compress-test/link', tarfile.SYMTYPE)]:
            with tempfile.TemporaryDirectory() as d:
                archive = Path(d) / 'source.tgz'
                with tarfile.open(archive, 'w:gz') as tar:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.linkname = '/etc/passwd' if kind == tarfile.SYMTYPE else ''
                    tar.addfile(entry, io.BytesIO())
                with self.assertRaises(ValueError):
                    m.unpack_decoder(archive, Path(d) / 'out', 'test')

    def test_work_lock_excludes_concurrent_writer(self):
        with tempfile.TemporaryDirectory() as d:
            with acquire.work_lock(Path(d)):
                with self.assertRaises(BlockingIOError):
                    with acquire.work_lock(Path(d)):
                        self.fail('Second writer admitted')

    def test_existing_prefix_and_repo_output_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'drive_c').mkdir()
            with self.assertRaises(ValueError):
                acquire.external_path(root / 'innocent-looking-subfolder')
        with self.assertRaises(ValueError):
            acquire.external_path(acquire.ROOT / 'acquisition')

    def test_tool_download_boundary_is_separate_from_microsoft(self):
        m.tool_source_url('https://codeload.github.com/coderforlife/ms-compress/tar.gz/abc')
        for url in ('https://codeload.github.com/other/repo/tar.gz/abc',
                    'http://codeload.github.com/coderforlife/ms-compress/tar.gz/abc',
                    'https://evil.test/coderforlife/ms-compress/tar.gz/abc'):
            with self.assertRaises(ValueError):
                m.tool_source_url(url)


if __name__ == '__main__':
    unittest.main()
