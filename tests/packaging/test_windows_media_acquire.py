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
    def test_busy_lock_explains_contention_and_is_reusable_after_exit(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            with m.work_lock(work):
                with self.assertRaisesRegex(RuntimeError, 'shared runtime busy'):
                    with m.work_lock(work, busy_message='shared runtime busy'):
                        self.fail('Two writers acquired the same lock')
            with m.work_lock(work):
                pass
            # EAGAIN from an operation inside the lock is not lock contention.
            with self.assertRaises(BlockingIOError):
                with m.work_lock(work):
                    raise BlockingIOError(11, 'operation failed')

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

    def test_chunked_response_without_length_still_checks_size_and_hash(self):
        for data, error in ((b'abcdef', None), (b'abc', 'Truncated'),
                            (b'abcdefg', 'exceeds'), (b'badbad', 'sha256 mismatch')):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
                response = Response(data);response.headers = {'Transfer-Encoding': 'chunked'}
                work = Path(d)
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        m.download_asset(work, self.asset(b'abcdef'), Opener(response), attempts=1)
                    self.assertFalse((work / 'fixture.esd').exists())
                else:
                    m.download_asset(work, self.asset(data), Opener(response))
                    self.assertEqual((work / 'fixture.esd').read_bytes(), data)

    def test_chunked_resume_still_requires_exact_content_range(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
            work = Path(d);(work / 'fixture.esd.partial').write_bytes(b'abc')
            response = Response(b'def', 206, 'bytes 3-5/6')
            del response.headers['Content-Length']
            m.download_asset(work, self.asset(b'abcdef'), Opener(response))
            self.assertEqual((work / 'fixture.esd').read_bytes(), b'abcdef')

    def test_truncated_transfer_never_promoted(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
            work = Path(d)
            with self.assertRaisesRegex(ValueError, 'Truncated'):
                m.download_asset(work, self.asset(b'abcdef'), Opener(Response(b'abc', size=6)), attempts=1)
            self.assertFalse((work / 'fixture.esd').exists())

    def test_truncated_download_resumes_before_transaction_can_roll_back(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'), patch.object(m.time, 'sleep'):
            responses=iter((Response(b'abc',size=6),Response(b'def',206,'bytes 3-5/6')))
            requests=[]
            class ResumingOpener:
                def open(self,request,timeout):
                    requests.append(request);return next(responses)
            work=Path(d)
            m.download_asset(work,self.asset(b'abcdef'),ResumingOpener())
            self.assertEqual([r.get_header('Range') for r in requests],[None,'bytes=3-'])
            self.assertEqual((work/'fixture.esd').read_bytes(),b'abcdef')
            self.assertFalse((work/'fixture.esd.partial').exists())

    def test_retry_exhaustion_retains_partial_and_never_promotes(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'), patch.object(m.time, 'sleep'):
            class EmptyOpener:
                calls=0
                def open(self,request,timeout):
                    self.calls+=1;return Response(b'',size=6)
            opener=EmptyOpener();work=Path(d)
            with self.assertRaisesRegex(m.InterruptedDownload,'after 3 attempts'):
                m.download_asset(work,self.asset(b'abcdef'),opener,attempts=3)
            self.assertEqual(opener.calls,3);self.assertFalse((work/'fixture.esd').exists())
            self.assertEqual((work/'fixture.esd.partial').read_bytes(),b'')

    def test_http_retries_only_transient_errors_and_never_bad_hashes(self):
        for code,retry in ((403,False),(404,False),(429,True),(503,True)):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as d,patch.object(m,'space_check'),patch.object(m.time,'sleep'):
                class HTTPFailure:
                    calls=0
                    def open(self,request,timeout):
                        self.calls+=1
                        if self.calls==1:raise m.urllib.error.HTTPError(Response.url,code,'fixture',{},None)
                        return Response(b'abcdef')
                opener=HTTPFailure()
                if retry:m.download_asset(Path(d),self.asset(b'abcdef'),opener)
                else:
                    with self.assertRaises(m.urllib.error.HTTPError):m.download_asset(Path(d),self.asset(b'abcdef'),opener)
                self.assertEqual(opener.calls,2 if retry else 1)

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
