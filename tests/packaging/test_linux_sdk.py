import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import linux_sdk as sdk
import linux_probe as probe


def archive(path, entries):
    with tarfile.open(path, 'w') as tar:
        for name, data, kind in entries:
            m = tarfile.TarInfo(name)
            if kind == 'link':
                m.type, m.linkname = tarfile.SYMTYPE, data
                tar.addfile(m)
            elif kind == 'device':
                m.type = tarfile.CHRTYPE
                tar.addfile(m)
            else:
                m.size = len(data)
                tar.addfile(m, io.BytesIO(data))


class SdkTests(unittest.TestCase):
    def test_guest_absolute_links_stay_inside_private_root(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sdk, 'space_check'):
            p = Path(tmp)
            archive(p / 'sdk.tar', [('usr/lib/test', b'private', 'file'),
                                     ('lib', '/usr/lib', 'link')])
            sdk.extract_root(p / 'sdk.tar', p / 'out')
            self.assertEqual((p / 'out/lib/test').read_bytes(), b'private')
            self.assertEqual((p / 'out/lib').readlink(), Path('usr/lib'))

    def test_sdk_archive_rejects_escape_duplicate_devices_and_size(self):
        cases = [[('../escape', b'x', 'file')], [('/escape', b'x', 'file')],
                 [('x', b'x', 'file'), ('x', b'x', 'file')], [('dev/x', b'', 'device')],
                 [('x', '../../outside', 'link')], [('large', b'123456', 'file')]]
        for entries in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as tmp, patch.object(sdk, 'space_check'):
                p = Path(tmp); archive(p / 'sdk.tar', entries)
                with self.assertRaises((ValueError, tarfile.FilterError)):
                    sdk.extract_root(p / 'sdk.tar', p / 'out', limit=5)
                self.assertFalse((p / 'outside').exists())

    def test_derive_refuses_unexpected_target_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sdk, 'verify_runtime', return_value={}):
            p = Path(tmp); built = p / 'built'; built.mkdir()
            report = {'schema': 1, 'lock_sha256': sdk.digest_file(sdk.LOCK)['sha256'],
                      'modules': {'dlls/ntdll/ntdll.so': {'sha256': 'unapproved'}}}
            (built / 'build.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'provenance'):
                sdk.derive(p / 'base', built, p / 'out')
            self.assertFalse((p / 'out').exists())

    def test_derive_refuses_tampered_module_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sdk, 'verify_runtime', return_value={}):
            p = Path(tmp); built = p / 'built'; built.mkdir()
            for target in sdk.TARGETS:
                path = built / 'work/build' / target; path.parent.mkdir(parents=True); path.write_bytes(b'changed')
            report = {'schema': 1, 'lock_sha256': sdk.digest_file(sdk.LOCK)['sha256'],
                      'modules': {name: {'sha256': 'wrong'} for name in sdk.TARGETS}}
            (built / 'build.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'module changed'):
                sdk.derive(p / 'base', built, p / 'out')
            self.assertFalse((p / 'out').exists())

    def test_backend_probe_requires_baseline_or_verified_derivative(self):
        config = {'packaged_runtime': {'manifest_sha256': 'baseline'}}
        with patch.object(probe, 'digest_file', return_value={'sha256': 'derived'}):
            with patch.object(probe, 'verify_runtime', return_value={'derived_from': {'sha256': 'baseline'}}):
                probe.matching_runtime(config, Path('/wine'))
            with patch.object(probe, 'verify_runtime', return_value={'derived_from': {'sha256': 'other'}}):
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    probe.matching_runtime(config, Path('/wine'))

    def test_download_urls_are_exact_pins(self):
        sdk_lock, test_lock = json.loads(sdk.LOCK.read_text()), json.loads(probe.LOCK.read_text())
        for validator, url in ((sdk.pinned_url, sdk_lock['archive']['url']),
                               (sdk.pinned_url, sdk_lock['wine_source']['url']),
                               (probe.node_url, test_lock['node']['url'])):
            self.assertEqual(validator(url), url)
            with self.assertRaises(ValueError):
                validator(url + '?redirect=elsewhere')

    def test_container_keeps_required_feature_gates_and_private_loader(self):
        import os
        with patch.dict(os.environ, {'WINELOADER': '/bad/wine', 'LD_PRELOAD': '/bad.so',
                                     'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0'}):
            env = probe.probe_environment(Path('/linux'), Path('/wine'), Path('/tools'), Path('/out'),
                                          {'WINE_GHUB_INPUT_SITE': '1', 'WINE_GHUB_HOST_KEYBOARD': '1'})
        self.assertEqual(env['WINE_GHUB_INPUT_SITE'], '1')
        self.assertEqual(env['WINE_GHUB_HOST_KEYBOARD'], '1')
        self.assertEqual(env['WINELOADER'], '/wine/usr/lib/wine/x86_64-unix/wine')
        self.assertNotIn('LD_PRELOAD', env)
        self.assertNotIn('DISPLAY', env)
        self.assertNotIn('WAYLAND_DISPLAY', env)


if __name__ == '__main__':
    unittest.main()
