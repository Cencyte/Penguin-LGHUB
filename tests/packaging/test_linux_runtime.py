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
import linux_runtime as m


def tar_bytes(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:xz') as tar:
        for name, data, kind in entries:
            item = tarfile.TarInfo(name)
            if kind == 'link':
                item.type, item.linkname = tarfile.SYMTYPE, data
                tar.addfile(item)
            elif kind == 'device':
                item.type = tarfile.CHRTYPE
                tar.addfile(item)
            else:
                item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
    return buf.getvalue()


def deb_bytes(payload):
    name = b'data.tar.xz/'
    header = (name.ljust(16) + b'0'.ljust(12) + b'0'.ljust(6) + b'0'.ljust(6)
              + b'100644'.ljust(8) + str(len(payload)).encode().ljust(10) + b'`\n')
    return b'!<arch>\n' + header + payload + (b'\n' if len(payload) % 2 else b'')


class LinuxRuntimeTests(unittest.TestCase):
    def test_official_urls_reject_redirect_and_host_confusion(self):
        lock = json.loads(m.LOCK.read_text())
        for url, validator in ((lock['archive']['url'], m.official_url),
                               (lock['support_packages'][0]['url'], m.support_url)):
            self.assertEqual(validator(url), url)
            for bad in (url.replace('https:', 'http:'), url + '?redirect=elsewhere',
                        url.replace('.com/', '.com.evil/').replace('.org/', '.org.evil/')):
                with self.assertRaises(ValueError):
                    validator(bad)

    def test_runtime_archive_refuses_escape_duplicate_and_device(self):
        cases = [[('../escape', b'x', 'file')],
                 [(m.DEPOT + '/link', '/etc', 'link')],
                 [(m.DEPOT + '/x', b'x', 'device')],
                 [(m.DEPOT + '/x', b'a', 'file'), (m.DEPOT + '/x', b'b', 'file')]]
        for entries in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); archive = p / 'input.tar.xz'; archive.write_bytes(tar_bytes(entries))
                output = p / 'out'; output.mkdir()
                with patch.object(m, 'space_check'), self.assertRaises(ValueError):
                    m.extract(archive, output)
                self.assertEqual(list(output.iterdir()), [])

    def test_deb_extract_keeps_library_links_and_notices(self):
        entries = [('usr/lib/x86_64-linux-gnu/libtest.so.1.0', b'fixture', 'file'),
                   ('usr/lib/x86_64-linux-gnu/libtest.so.1', 'libtest.so.1.0', 'link'),
                   ('usr/share/doc/libtest/copyright', b'license', 'file')]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); archive = p / 'test.deb'; archive.write_bytes(deb_bytes(tar_bytes(entries)))
            m.extract_support(archive, p / 'out')
            self.assertEqual((p / 'out/usr/lib/x86_64-linux-gnu/libtest.so.1').read_bytes(), b'fixture')
            self.assertEqual((p / 'out/usr/share/doc/libtest/copyright').read_bytes(), b'license')

    def test_deb_extract_refuses_traversal_and_malformed_header(self):
        for payload in (b'broken', deb_bytes(tar_bytes([('../escape', b'x', 'file')])),
                        deb_bytes(tar_bytes([('usr/link', '/etc', 'link')]))):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); archive = p / 'test.deb'; archive.write_bytes(payload)
                with self.assertRaises(ValueError):
                    m.extract_support(archive, p / 'out')

    def test_inventory_detects_content_permission_and_external_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); lib = root / m.DEPOT / 'lib'; lib.parent.mkdir()
            lib.write_bytes(b'one'); lib.chmod(0o755)
            first = m.tree(root)
            lib.chmod(0o644)
            self.assertNotEqual(m.tree(root), first)
            lib.chmod(0o755); lib.write_bytes(b'two')
            self.assertNotEqual(m.tree(root), first)
            (lib.parent / 'bad').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                m.tree(root)

    def test_missing_dependency_is_failure_even_when_ldd_exits_zero(self):
        result = m.classify_ldd(' libunwind.so.8 => not found\n', 0, ['libunwind.so.8'])
        self.assertFalse(result['linked'])
        self.assertEqual(result['missing'], ['libunwind.so.8'])
        self.assertFalse(m.classify_ldd("version `GLIBC_9.9' not found", 0, ['libc.so.6'])['linked'])
        self.assertTrue(m.classify_ldd('statically linked', 0, [])['linked'])

    def test_audit_drops_live_session_and_injected_loaders(self):
        injected = {k: 'bad' for k in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY',
                    'DBUS_SESSION_BUS_ADDRESS', 'LD_PRELOAD', 'WINESERVER', 'LD_LIBRARY_PATH')}
        with patch.dict(os.environ, injected):
            env = m.clean_environment()
        self.assertFalse(set(injected) & set(env))
        cmd = m.isolated_command(Path('/linux'), Path('/result'), ['true'])
        for item in ('--unshare-net', '--unshare-pid', '--graphics-provider=', '--dev'):
            self.assertIn(item, cmd)
        self.assertNotIn('sudo', cmd)


if __name__ == '__main__':
    unittest.main()
