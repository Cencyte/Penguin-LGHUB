import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'media', Path(__file__).resolve().parents[2] / 'tools/packaging/windows_media_acquire.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class Response(io.BytesIO):
    url = 'https://download.microsoft.com/fixture'

    def __init__(self, data, status=200, content_range=None, size=None):
        super().__init__(data)
        self.status = status
        self.headers = {'Content-Length': str(len(data) if size is None else size)}
        if content_range:
            self.headers['Content-Range'] = content_range


class Opener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        self.request = request
        return self.response


class MediaTests(unittest.TestCase):
    def asset(self, data):
        return {'url': Response.url, 'local_name': 'fixture.esd', 'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest()}

    def test_resume_checks_range_and_complete_hash(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
            work = Path(d)
            (work / 'fixture.esd.partial').write_bytes(b'abc')
            opener = Opener(Response(b'def', 206, 'bytes 3-5/6'))
            m.download_asset(work, self.asset(b'abcdef'), opener)
            self.assertEqual(opener.request.get_header('Range'), 'bytes=3-')
            self.assertEqual((work / 'fixture.esd').read_bytes(), b'abcdef')

    def test_ignored_or_wrong_range_does_not_append(self):
        for response in (Response(b'abcdef'), Response(b'def', 206, 'bytes 0-2/6')):
            with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
                work = Path(d)
                partial = work / 'fixture.esd.partial'
                partial.write_bytes(b'abc')
                with self.assertRaises(ValueError):
                    m.download_asset(work, self.asset(b'abcdef'), Opener(response))
                self.assertEqual(partial.read_bytes(), b'abc')

    def test_wrong_hash_never_promoted(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
            work = Path(d)
            with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
                m.download_asset(work, self.asset(b'good'), Opener(Response(b'evil')))
            self.assertFalse((work / 'fixture.esd').exists())

    def test_truncated_transfer_never_promoted(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
            work = Path(d)
            with self.assertRaisesRegex(ValueError, 'Truncated'):
                m.download_asset(work, self.asset(b'abcdef'), Opener(Response(b'abc', size=6)))
            self.assertFalse((work / 'fixture.esd').exists())

    def test_disk_budget_includes_remaining_bytes(self):
        with patch.object(m.shutil, 'disk_usage') as disk:
            disk.return_value.free = m.FLOOR + 100
            with self.assertRaises(RuntimeError):
                m.space_check('/tmp', 101)
            m.space_check('/tmp', 100)

    def test_official_download_boundary(self):
        m.official_url('https://catalog.sf.dl.delivery.mp.microsoft.com/test')
        for url in ('http://download.microsoft.com/test', 'https://microsoft.com.evil/test',
                    'https://user:secret@microsoft.com/test', 'file:///tmp/test',
                    'https://random.blob.core.windows.net/test'):
            with self.assertRaises(ValueError):
                m.official_url(url)

    def test_symlink_and_filename_escape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / 'fixture.esd').symlink_to('/dev/null')
            with self.assertRaises(ValueError):
                m.check_asset(work / 'fixture.esd', self.asset(b''))
            asset = self.asset(b'abc')
            asset['local_name'] = '../escape'
            with self.assertRaises(ValueError):
                m.download_asset(work, asset)

    def test_same_version_wrong_hash_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            data = bytearray(512)
            data[:2] = b'MZ'
            struct.pack_into('<I', data, 0x3c, 64)
            data[64:68] = b'PE\0\0'
            struct.pack_into('<H', data, 68, 0x8664)
            struct.pack_into('<IIII', data, 256, 0xfeef04bd, 0x10000, 10 << 16, (26100 << 16) | 8737)
            p = work / 'd2d1.dll'
            p.write_bytes(data)
            self.assertEqual(m.pe_info(p), {'machine': '0x8664', 'version': '10.0.26100.8737'})
            lock = {'files': {'d2d1.dll': {'sha256': '0' * 64, 'size': 512,
                                          'machine': '0x8664', 'version': '10.0.26100.8737'}}}
            with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
                m.verify_dlls(work, lock)


if __name__ == '__main__':
    unittest.main()
