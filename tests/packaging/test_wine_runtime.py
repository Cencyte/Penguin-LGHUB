import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import wine_runtime as m
import prepare_clean_prefix as prefix


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        for name, data, kind in entries:
            item = tarfile.TarInfo(name)
            if kind == 'link':
                item.type, item.linkname = tarfile.SYMTYPE, data
                tar.addfile(item)
            elif kind == 'device':
                item.type = tarfile.CHRTYPE
                tar.addfile(item)
            else:
                item.size, item.mode = len(data), 0o600 if kind == 'private' else 0o755
                tar.addfile(item, io.BytesIO(data))
    stream.seek(0)
    return stream


class RuntimeTests(unittest.TestCase):
    def test_official_archive_boundary(self):
        good = 'https://archive.archlinux.org/packages/w/wine/wine-10.20-2-x86_64.pkg.tar.zst'
        self.assertEqual(m.official_url(good), good)
        for bad in (good.replace('https:', 'http:'), good.replace('archlinux.org', 'archlinux.org.evil'),
                    good + '?redirect=elsewhere', good.replace('/w/wine/', '/g/glibc/')):
            with self.assertRaises(ValueError):
                m.official_url(bad)

    def test_extraction_selects_runtime_without_executing_package_hooks(self):
        entries = [('.PKGINFO', b'pkgname = wine', 'file'), ('.INSTALL', b'exit 42', 'file'),
                   ('usr/include/wine/header.h', b'header', 'file'),
                   ('usr/bin/wine', b'fixture', 'file'), ('usr/bin/wineboot', 'wine', 'link')]
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'space_check'):
            root = Path(tmp)
            m.unpack(archive(entries), root)
            self.assertEqual((root / 'usr/bin/wineboot').read_bytes(), b'fixture')
            self.assertTrue(os.access(root / 'usr/bin/wine', os.X_OK))
            self.assertTrue((root / 'provenance/PKGINFO').is_file())
            self.assertFalse((root / '.INSTALL').exists())
            self.assertFalse((root / 'usr/include').exists())

    def test_extraction_rejects_traversal_links_devices_and_duplicates(self):
        examples = [[('../outside', b'a', 'file')], [('/usr/bin/wine', b'a', 'file')],
                    [('usr/bin/escape', '../../outside', 'link')], [('usr/bin/device', '', 'device')],
                    [('usr/bin/wine', b'a', 'file'), ('usr/bin/wine', b'b', 'file')]]
        for entries in examples:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as tmp, patch.object(m, 'space_check'):
                with self.assertRaises(ValueError):
                    m.unpack(archive(entries), Path(tmp))

    def test_inventory_detects_bytes_permissions_and_external_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); binary = root / 'wine'
            binary.write_bytes(b'one'); binary.chmod(0o755)
            before = m.inventory(root)
            binary.chmod(0o644)
            self.assertNotEqual(m.inventory(root), before)
            binary.chmod(0o755); binary.write_bytes(b'two')
            self.assertNotEqual(m.inventory(root), before)
            (root / 'external').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                m.inventory(root)

    def test_runtime_environment_pins_server_loader_and_drops_injections(self):
        env = m.runtime_environment(Path('/bundle'), Path('/prefix'), {
            'PATH': '/usr/bin', 'WINESERVER': '/wrong', 'WINEDLLPATH': '/wrong',
            'LD_PRELOAD': '/bad', 'LD_LIBRARY_PATH': '/bad', 'WINEDEBUG': '-all',
            'WINEDLLOVERRIDES': 'example=n', 'GHUB_TEST': 'yes'})
        self.assertEqual(env['WINESERVER'], '/bundle/usr/bin/wineserver')
        self.assertEqual(env['WINELOADER'], '/bundle/usr/lib/wine/x86_64-unix/wine')
        self.assertEqual(env['WINEDLLPATH'], '/bundle/usr/lib/wine')
        self.assertEqual(env['WINEPREFIX'], '/prefix')
        self.assertTrue(env['PATH'].startswith('/bundle/usr/bin:'))
        self.assertNotIn('LD_PRELOAD', env)
        self.assertEqual(env['GHUB_TEST'], 'yes')
        self.assertEqual(env['WINEDLLOVERRIDES'], 'example=n')

    def test_lock_preserves_all_baseline_artifacts_and_matched_protocol(self):
        lock = json.loads(m.LOCK.read_text())
        baseline = prefix.compatibility_files(json.loads(prefix.BASELINE.read_text()))
        actual = {name: spec['sha256'] for kind in ('patches','markers','supplements')
                  for name,spec in lock[kind].items()}
        self.assertEqual(actual, baseline)
        self.assertEqual(len(lock['patches']), 20)
        targets = [s['target'] for s in lock['patches'].values()]
        self.assertEqual(len(targets), len(set(targets)))
        for name in ('usr/bin/wineserver', 'usr/lib/wine/x86_64-unix/ntdll.so',
                     'usr/lib/wine/x86_64-windows/ntdll.dll'):
            self.assertIn(name, targets)

    def test_packaged_sandbox_hides_host_wine_and_freezes_only_candidate(self):
        with patch.object(Path, 'is_dir', return_value=True), patch.object(Path, 'exists', return_value=True):
            cmd = prefix.sandbox(Path('/candidate'), ['wine', '--version'], True, True)
        self.assertIn('/usr/lib/wine', cmd)
        self.assertIn('/usr/share/wine', cmd)
        self.assertIn('/usr/bin/wineserver', cmd)
        self.assertIn('/dev/null', cmd)
        self.assertEqual(cmd[-9:], ['--ro-bind', '/candidate/prefix/' + prefix.APP,
                                  '/candidate/prefix/' + prefix.APP,
                                  '--ro-bind', '/candidate/prefix/' + prefix.DATA,
                                  '/candidate/prefix/' + prefix.DATA, '--', 'wine', '--version'])
        self.assertNotIn('sudo', cmd)

    def test_edited_receipt_cannot_replace_pinned_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bundle = root / 'bundle'; bundle.mkdir()
            artifact = bundle / 'wine'; artifact.write_bytes(b'expected')
            spec = {'target': 'wine', 'sha256': m.digest_file(artifact)['sha256']}
            lock = root / 'lock.json'
            lock.write_text(json.dumps({'patches': {'source': spec}, 'markers': {}, 'supplements': {}}))
            report = {'schema': 1, 'kind': 'penguin-private-wine-runtime', 'supplements': {},
                      'lock_sha256': m.digest_file(lock)['sha256'], 'files': m.inventory(bundle)}
            receipt = bundle / 'runtime.json'; receipt.write_text(json.dumps(report))
            with patch.object(m, 'LOCK', lock):
                m.verify_runtime(bundle)
                artifact.write_bytes(b'changed')
                report['files'] = m.inventory(bundle)
                receipt.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, 'pinned compatibility'):
                    m.verify_runtime(bundle)

    def test_restore_refuses_wrong_hash_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package = root / 'package.zst'; package.write_bytes(b'changed')
            with patch.object(m.subprocess, 'Popen') as run:
                with self.assertRaises(ValueError):
                    m.restore_runtime(package, '0' * 64, root / 'new')
                run.assert_not_called()
                self.assertFalse((root / 'new').exists())

    def test_bundle_restoration_retains_receipt_and_supplementary_files(self):
        entries = [('./runtime.json', b'{}', 'private'),
                   ('./compatibility/helper.dll', b'helper', 'file')]
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'space_check'):
            root = Path(tmp)
            m.unpack(archive(entries), root, bundle=True)
            self.assertEqual((root / 'runtime.json').read_bytes(), b'{}')
            self.assertEqual((root / 'runtime.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / 'compatibility/helper.dll').read_bytes(), b'helper')


if __name__ == '__main__':
    unittest.main()
