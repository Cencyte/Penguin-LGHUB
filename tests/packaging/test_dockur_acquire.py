import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

SPEC = importlib.util.spec_from_file_location('acquire', Path(__file__).resolve().parents[2] / 'tools/packaging/dockur_acquire.py')
a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)


class AcquisitionTests(unittest.TestCase):
    def test_microsoft_https(self):
        a.official_url('https://software-static.download.prss.microsoft.com/test.iso')

    def test_reject_non_official_and_misleading_urls(self):
        for url in ['http://microsoft.com/file.iso', 'https://microsoft.com.evil.test/x',
                    'https://evil.test/microsoft.com/x', 'file:///tmp/a',
                    'https://user:password@microsoft.com/x', 'https://microsoft.com:444/x']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                a.official_url(url)

    def test_redirect_does_not_escape_official_source(self):
        request = urllib.request.Request('https://microsoft.com/iso')
        with self.assertRaises(ValueError):
            a.MicrosoftRedirects().redirect_request(request, None, 302, 'Found', {}, 'https://mirror.example/x.iso')

    def test_disk_reservation_checked_before_start(self):
        with patch.object(a.shutil, 'disk_usage') as usage:
            usage.return_value.free = 99 * a.GIB
            with self.assertRaises(RuntimeError):
                a.space_check(['/tmp'], 80 * a.GIB)
            usage.return_value.free = 101 * a.GIB
            a.space_check(['/tmp'], 80 * a.GIB)

    def test_recipe_isolated_and_pinned(self):
        c = a.config(Path('/control'), Path('/iso/media.iso'), Path('/data'))
        s = c['services']['windows']
        self.assertIn('@sha256:', s['image'])
        self.assertTrue(c['networks']['default']['internal'])
        self.assertEqual(s['ports'], ['127.0.0.1:18006:8006'])
        self.assertNotIn('privileged', s)
        self.assertNotIn('network_mode', s)
        self.assertEqual(s['environment']['DISK_FMT'], 'qcow2')
        self.assertEqual(s['volumes'][-1]['target'], '/custom.iso')
        self.assertTrue(s['volumes'][-1]['read_only'])
        self.assertEqual(s['environment']['MIDO'], 'N')

    def make_collection(self, root):
        folder = root / 'shared/collected'
        folder.mkdir(parents=True)
        (root / 'acquisition.json').write_text(json.dumps({'data_dir': str(root)}))
        files = []
        for name in list(a.LOCK['baseline_sha256']) + a.LOCK['fonts']:
            p = folder / name
            p.write_bytes(b'fixture data; not a Windows file')
            files.append({'name': name, 'size': p.stat().st_size, 'sha256': a.sha256(p)})
        (folder / 'guest-manifest.json').write_text(json.dumps({'files': files}), encoding='utf-8-sig')
        return folder, files

    def test_different_versions_never_qualify_as_working_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_collection(root)
            result = a.verify_collection(root)
            self.assertEqual(result['collected_files'], 12)
            self.assertEqual(result['dll_baseline_matches'], 0)
            self.assertFalse(result['wine_compatibility_tested'])
            self.assertEqual(result['license_clearance'], 'unresolved')

    def test_incomplete_duplicate_and_path_escape_rejected(self):
        for mode in ['missing', 'duplicate', 'path']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                folder, files = self.make_collection(root)
                if mode == 'missing': files.pop()
                elif mode == 'duplicate': files[-1] = files[0]
                else: files[-1]['name'] = '../../outside'
                (folder / 'guest-manifest.json').write_text(json.dumps({'files': files}))
                with self.assertRaises(ValueError): a.verify_collection(root)

    def test_tampering_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            folder, _ = self.make_collection(root)
            (folder / 'd2d1.dll').write_bytes(b'modified')
            with self.assertRaises(ValueError): a.verify_collection(root)

    def test_low_disk_stops_only_owned_container(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'shared').mkdir()
            iso = root / 'media.iso'
            iso.write_bytes(b'fixture')
            meta = {'data_dir': str(root), 'iso': str(iso), 'iso_sha256': a.sha256(iso),
                    'license_basis': 'registered-evaluation'}
            args = SimpleNamespace(workspace=d, accept_windows_license=True, max_seconds=30)
            with patch.object(a, 'load_work', return_value=(root, meta)), \
                 patch.object(a, 'space_check', side_effect=[None, None, RuntimeError('disk floor')]), \
                 patch.object(a, 'run', return_value=SimpleNamespace(stdout='/tmp')), \
                 patch.object(a, 'container_id', return_value=('owned-id', {})), \
                 patch.object(a.signal, 'signal'), \
                 patch.object(a.subprocess, 'run') as execute, \
                 patch.object(a.Path, 'read_text', return_value='MemAvailable: 16777216 kB'):
                with self.assertRaisesRegex(RuntimeError, 'disk floor'):
                    a.supervise(args)
                execute.assert_any_call(['docker', 'stop', '-t', '30', 'owned-id'], check=False)
                self.assertEqual(len(execute.call_args_list), 2)  # own logs + own stop

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            folder, _ = self.make_collection(root)
            (folder / 'd2d1.dll').unlink()
            (folder / 'd2d1.dll').symlink_to(folder / 'ninput.dll')
            with self.assertRaises(ValueError): a.verify_collection(root)


if __name__ == '__main__':
    unittest.main()
