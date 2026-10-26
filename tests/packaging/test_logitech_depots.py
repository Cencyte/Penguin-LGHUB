import base64
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import logitech_depots as m
import logitech_capsule as c

KID = '00000000-0000-0000-0000-000000000001'
KEY = bytes(range(16))  # Synthetic fixture, not a vendor key.
PLAIN = b'Penguin-LGHUB synthetic capsule fixture\n'
SHA = hashlib.sha256(PLAIN).hexdigest()
# Independently generated with cryptography's AES/GCM implementation.
CIPHER = bytes.fromhex('13880152b1f61efff68c9a7c9aea25c916ea436d0703a65fe1d0a56afd2db4a3554b2d3e493c8a01')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive(files):
    header = json.dumps({'files': [{'name': n, 'mode': 0o100666} for n, _ in files]}).encode()
    return struct.pack('<II', m.PLAIN, len(header)) + header + b''.join(
        struct.pack('<I', len(data)) + data for _, data in files)


def spec(data, files):
    return {'size': len(data), 'sha256': sha(data), 'expected_files': files}


class DepotTests(unittest.TestCase):
    def inspect(self, data, files):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'fixture.depot'
            path.write_bytes(data)
            return m.inspect_depot(path, spec(data, files))

    def test_plain_archive_round_trip(self):
        data = archive([('dir/file', b'payload'), ('empty', b'')])
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
            path = Path(d) / 'fixture.depot'
            path.write_bytes(data)
            index = m.inspect_depot(path, spec(data, ['dir/file', 'empty']))
            records = m.extract_depot(path, Path(d) / 'out', index)
            self.assertEqual((Path(d) / 'out/dir/file').read_bytes(), b'payload')
            self.assertEqual(records[1]['sha256'], sha(b''))
            with self.assertRaises(ValueError):
                m.extract_depot(path, Path(d) / 'out', index)

    def test_untrusted_paths_and_collisions(self):
        for name in ('../escape', '/abs', 'a/../b', 'a\\b', 'a:b', 'CON.txt',
                     'a//b', 'file.', 'file ', 'a\x00b', 'a?b', 'a*b', 'a|b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.inspect(archive([(name, b'')]), [name])
        for names in (['a', 'A'], ['a', 'a/b'], ['D/a', 'd/b']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.inspect(archive([(n, b'') for n in names]), names)

    def test_truncation_trailing_bytes_and_file_set(self):
        data = archive([('a', b'abcdef')])
        for invalid in (data[:-1], data + b'x', data[:7], struct.pack('<II', m.PLAIN, m.MAX_INDEX + 1)):
            with self.assertRaises(ValueError):
                self.inspect(invalid, ['a'])
        with self.assertRaises(ValueError):
            self.inspect(data, ['other'])

    def test_hash_is_checked_before_archive_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'file'
            path.write_bytes(b'evil')
            with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
                m.inspect_depot(path, spec(b'good', []))

    def test_symlink_and_duplicate_json_rejected(self):
        header = b'{"files":[],"files":[]}'
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON'):
            self.inspect(struct.pack('<II', m.PLAIN, len(header)) + header, [])
        header = json.dumps({'files': [{'name': 'link', 'mode': 0o120777}]}).encode()
        with self.assertRaisesRegex(ValueError, 'Nonregular'):
            self.inspect(struct.pack('<II', m.PLAIN, len(header)) + header, ['link'])

    def test_dependency_closure_and_failure(self):
        lock = {'selections': {'core': ['core']}, 'depots': {
            'core': {'depends_on': ['a', 'b']}, 'a': {'depends_on': ['b']}, 'b': {'depends_on': []}}}
        self.assertEqual(m.select_depots(lock, 'core'), ['b', 'a', 'core'])
        lock['depots']['b']['depends_on'] = ['core']
        with self.assertRaisesRegex(ValueError, 'cycle'):
            m.select_depots(lock, 'core')
        lock['depots']['b']['depends_on'] = ['missing']
        with self.assertRaisesRegex(ValueError, 'Unpinned'):
            m.select_depots(lock, 'core')

    def test_download_host_is_exact(self):
        good = 'https://updates.ghub.logitechg.com/depots/00000000-0000-0000-0000-000000000000/core.depot'
        self.assertEqual(m.official_url(good), good)
        for bad in (good.replace('https:', 'http:'), good + '?redirect=1',
                    good.replace('.com/', '.com.evil/'), good.replace('https://', 'https://user@')):
            with self.assertRaises(ValueError):
                m.official_url(bad)

    def test_cipher_known_answer_and_tampering(self):
        plain = c.decrypt(CIPHER, SHA, KID, {KID: KEY})
        self.assertEqual(plain, PLAIN)
        m.check_member(plain, SHA, {})
        for data in (b'changed', c.decrypt(CIPHER, SHA, KID, {KID: bytes(16)})):
            with self.assertRaises(ValueError):
                m.check_member(data, SHA, {})
        with self.assertRaisesRegex(ValueError, 'unpinned'):
            c.decrypt(CIPHER, SHA, KID, {})

    def test_nested_capsule_integrity_and_bounds(self):
        h = json.dumps({'key-id': KID, 'file-sha': SHA}).encode()
        nested = struct.pack('<II', m.FILE_CAPSULE, len(h)) + h + struct.pack('<I', len(CIPHER)) + CIPHER
        m.check_member(nested, SHA, {KID: KEY})
        for invalid in (nested[:-1], nested + b'X', nested[:-1] + bytes([nested[-1] ^ 1])):
            with self.assertRaises(ValueError):
                m.check_member(invalid, SHA, {KID: KEY})
        with self.assertRaises(ValueError):
            m.check_member(nested, '0' * 64, {KID: KEY})

    def test_encrypted_depot_round_trip_and_header_integrity(self):
        digest = '7c7dedde65827015e529189fa09aa11198160149c3322db14f5197826bc35be3'
        encrypted_header = bytes.fromhex(
            'd9e90e570a3f56c56398009f14e9dea10bcc5f339741c96d192b49f7180e4ec027f429bc3229f5ea44f9715'
            'fbee85cd5a182599e976e8bbfaa9211e557431115f7b99366ad94212ee81bfb1d5a8e075c33bb1048c3ae5c'
            'c89cd6f41e411fb4a8b7da3f6d9f1051bcdd69a4e6bd7ee9d46ebee962')
        header = json.dumps({'header-sha': digest, 'key-id': KID}).encode()
        data = (struct.pack('<II', m.CAPSULE, len(header)) + header
                + struct.pack('<I', len(encrypted_header)) + encrypted_header
                + struct.pack('<I', len(CIPHER)) + CIPHER)
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
            path = Path(d) / 'fixture.depot'
            path.write_bytes(data)
            index = m.inspect_depot(path, spec(data, ['fixture']), {KID: KEY})
            records = m.extract_depot(path, Path(d) / 'out', index, {KID: KEY})
            self.assertEqual(records[0]['sha256'], SHA)
            self.assertEqual((Path(d) / 'out/fixture').read_bytes(), PLAIN)
            with self.assertRaisesRegex(ValueError, 'index SHA-256'):
                m.inspect_depot(path, spec(data, ['fixture']), {KID: bytes(16)})

    def test_public_key_record_pins_allow_unrelated_additions(self):
        record = {'name': KID, 'key': base64.b64encode(KEY).decode(), 'version': 1}
        pin = {'access_group': 'fixture', 'required_keys': {KID: c.key_fingerprint(record)}}
        document = {'accessGroup': 'fixture', 'keys': [record, {'name': 'unrelated'}]}
        self.assertEqual(c.validate_keys(json.dumps(document).encode(), pin), {KID: KEY})
        record['key'] = base64.b64encode(bytes(16)).decode()
        with self.assertRaisesRegex(ValueError, 'changed'):
            c.validate_keys(json.dumps(document).encode(), pin)
        document['keys'] = []
        with self.assertRaisesRegex(ValueError, 'Missing'):
            c.validate_keys(json.dumps(document).encode(), pin)

    def test_layout_and_verification_fail_closed(self):
        manifest = {'installer': {'files': [
            {'source': 'file', 'destination': '{install_root}/file'},
            {'source': 'absent.dll', 'destination': '{install_root}/absent.dll'},
            {'source': 'file', 'destination': '{windows_root}/file'}], 'services': [{'name': 'fixture'}]}}
        data = archive([('manifest.json', json.dumps(manifest).encode()), ('file', b'content')])
        lock = {'application_version': 'fixture', 'build_id': '1', 'selections': {'core': ['core']},
                'depots': {'core': dict(spec(data, ['manifest.json', 'file']), local_name='core.depot', depends_on=[])}}
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'space_check'):
            work = Path(d) / 'cache'
            work.mkdir()
            (work / 'core.depot').write_bytes(data)
            output = Path(d) / 'stage'
            m.stage(work, output, lock, 'core')
            verified = m.verify_stage(output, lock)
            self.assertEqual(verified['depot_files_verified'], 2)
            report = json.loads((output / 'extraction.json').read_text())
            self.assertEqual(len(report['layout']['pending_installer_actions']), 3)
            target = output / 'application/LGHUB/file'
            target.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                m.verify_stage(output, lock)
            target.write_bytes(b'content')
            extra = target.with_name('extra')
            extra.write_bytes(b'')
            with self.assertRaises(ValueError):
                m.verify_stage(output, lock)
            extra.unlink()
            target.unlink()
            target.symlink_to(output / 'depots/core/file')
            with self.assertRaises(ValueError):
                m.verify_stage(output, lock)
            target.unlink()
            target.write_bytes(b'content')
            report['extracted']['core'] = report['extracted']['core'][:1]
            (output / 'extraction.json').write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                m.verify_stage(output, lock)


if __name__ == '__main__':
    unittest.main()
