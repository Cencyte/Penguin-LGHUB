import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import wayland_session
import xwayland_input as bridge


def elf(ei=True, portal=True):
    """Sectionless ELF fixture: dependencies and dynamic imports in PT_LOAD."""
    libraries = [b'libc.so.6']
    imports = []
    if ei:
        libraries.append(b'libei.so.1')
        imports += [b'ei_device_keyboard_key', b'ei_setup_backend_socket']
    if portal:
        libraries.append(b'liboeffis.so.1')
        imports.append(b'oeffis_create_session')
    strings = b'\0'
    offsets = []
    for name in libraries:
        offsets.append(len(strings))
        strings += name + b'\0'
    strings += b'\0'.join(imports) + b'\0'
    dynamic_offset = 64 + 2 * 56
    dynamic_size = (len(offsets) + 3) * 16
    string_offset = dynamic_offset + dynamic_size
    address = 0x400000
    dynamic = b''.join(struct.pack('<qQ', tag, value) for tag, value in
                       [(1, n) for n in offsets] + [(5, address + string_offset), (10, len(strings)), (0, 0)])
    ident = b'\x7fELF\x02\x01\x01' + bytes(9)
    header = ident + struct.pack('<HHIQQQIHHHHHH', 3, 62, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    total = string_offset + len(strings)
    load = struct.pack('<IIQQQQQQ', 1, 4, 0, address, address, total, total, 4096)
    table = struct.pack('<IIQQQQQQ', 2, 4, dynamic_offset, address + dynamic_offset, 0,
                        dynamic_size, dynamic_size, 8)
    return header + load + table + dynamic + strings


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.env = {'DISPLAY': ':3.0', 'PATH': '/nonexistent'}

    def server(self, pid=123, display=':3', ei=True, portal=True, enabled=False):
        process = self.proc / str(pid)
        process.mkdir()
        (process / 'comm').write_text('Xwayland\n')
        args = ['/usr/bin/Xwayland', display, '-displayfd', '9', '-rootless']
        if enabled:
            args.append('-enable-ei-portal')
        (process / 'cmdline').write_bytes(b'\0'.join(s.encode() for s in args) + b'\0')
        (process / 'environ').write_bytes(b'SECRET=must-not-be-logged\0')
        (process / 'exe').write_bytes(elf(ei, portal))
        return process

    def test_portal_build_and_enabled_route(self):
        self.server(enabled=True)
        report = bridge.inspect(self.env, self.proc)
        self.assertEqual(report['route'], 'portal')
        self.assertTrue(report['configuration_supported'])
        self.assertTrue(report['binary']['liboeffis_linked'])
        self.assertFalse(report['errors'])
        self.assertEqual(report['permission_granted'], 'not measured')
        self.assertFalse(report['key_delivery_tested'])

    def test_xtest_without_compiled_ei_is_a_blocker(self):
        self.server(ei=False, portal=False, enabled=True)
        report = bridge.inspect(self.env, self.proc)
        self.assertFalse(report['configuration_supported'])
        self.assertIn('installing libei alone', report['errors'][0])

    def test_compiled_but_not_enabled_is_a_blocker(self):
        self.server()
        report = bridge.inspect(self.env, self.proc)
        self.assertFalse(report['configuration_supported'])
        self.assertIn('neither an enabled', report['errors'][0])

    def test_portal_argument_does_not_supply_missing_oeffis(self):
        self.server(portal=False, enabled=True)
        report = bridge.inspect(self.env, self.proc)
        self.assertFalse(report['binary']['portal_bridge'])
        self.assertTrue(report['errors'])

    def test_private_eis_fd_needs_ei_but_not_oeffis_or_portal_flag(self):
        process = self.server(portal=False)
        (process / 'fd').mkdir()
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(self.root / 'eis'))
            (process / 'fd/102').symlink_to(self.root / 'eis')
            (process / 'environ').write_bytes(b'LIBEI_SOCKET=/proc/self/fd/102\0SECRET=hidden\0')
            report = bridge.inspect(self.env, self.proc)
        self.assertEqual(report['route'], 'private-eis-socket')
        self.assertTrue(report['configuration_supported'])
        self.assertNotIn('hidden', str(report))

    def test_socket_env_needs_a_real_socket(self):
        process = self.server()
        (process / 'environ').write_bytes(b'LIBEI_SOCKET=ordinary\0XDG_RUNTIME_DIR=' + os.fsencode(self.root) + b'\0')
        (self.root / 'ordinary').write_text('not a socket')
        self.assertFalse(bridge.inspect(self.env, self.proc)['configuration_supported'])
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(self.root / 'eis'))
            (process / 'environ').write_bytes(b'LIBEI_SOCKET=eis\0XDG_RUNTIME_DIR=' + os.fsencode(self.root) + b'\0')
            self.assertTrue(bridge.inspect(self.env, self.proc)['configuration_supported'])

    def test_match_the_display_not_first_process(self):
        self.server(pid=10, display=':1', ei=False, portal=False)
        self.server(pid=20, enabled=True)
        report = bridge.inspect(self.env, self.proc)
        self.assertEqual(report['pid'], 20)
        self.assertTrue(report['configuration_supported'])

    def test_ambiguous_or_remote_or_no_match_is_unverified(self):
        self.server(pid=10, enabled=True)
        self.server(pid=20, enabled=True)
        for display in (':3', ':12', 'localhost:3', ''):
            with self.subTest(display=display):
                report = bridge.inspect(dict(self.env, DISPLAY=display), self.proc)
                self.assertFalse(report['server_identified'])
                self.assertIsNone(report['configuration_supported'])
                self.assertTrue(report['warnings'])

    def test_installed_binary_never_stands_in_for_active_server(self):
        path = self.root / 'installed'
        path.write_bytes(elf())
        with patch.object(bridge.shutil, 'which', return_value=str(path)):
            report = bridge.inspect(self.env, self.proc)
        self.assertTrue(report['installed_binary']['ei_bridge'])
        self.assertIsNone(report['configuration_supported'])

    def test_unreadable_active_executable_does_not_fall_back_to_installed(self):
        process = self.server(enabled=True)
        (process / 'exe').unlink()
        with patch.object(bridge.shutil, 'which') as which:
            report = bridge.inspect(self.env, self.proc)
        which.assert_not_called()
        self.assertIsNone(report['configuration_supported'])
        self.assertTrue(report['warnings'])
        self.assertFalse(report['errors'])

    def test_denied_server_environment_is_unknown_not_missing_socket(self):
        self.server()
        with patch.object(bridge, 'socket_route', side_effect=PermissionError('denied')):
            report = bridge.inspect(self.env, self.proc)
        self.assertIsNone(report['configuration_supported'])
        self.assertTrue(report['warnings'])
        self.assertFalse(report['errors'])

    def test_truncated_and_unsupported_elf_are_unverified(self):
        path = self.root / 'binary'
        valid = elf()
        for data in (b'#!script', valid[:60], valid[:-1], valid[:4] + b'\x01' + valid[5:]):
            with self.subTest(length=len(data)):
                path.write_bytes(data)
                report = bridge.inspect_binary(path)
                self.assertIsNone(report['ei_bridge'])
                self.assertIn('unverified_reason', report)

    def test_current_process_image_is_used_after_disk_binary_changes(self):
        process = self.server(enabled=True)
        image = self.root / 'old-image'
        (process / 'exe').rename(image)
        (process / 'exe').symlink_to(image)
        disk = self.root / 'Xwayland'
        disk.write_bytes(elf(False, False))
        with patch.object(bridge.shutil, 'which', return_value=str(disk)):
            self.assertTrue(bridge.inspect(self.env, self.proc)['configuration_supported'])

    def test_bridge_failure_propagates_even_with_xtest_and_portal(self):
        def respond(args, env):
            if args[0] == 'xdpyinfo':
                return 0, '    XWAYLAND\n    XTEST\n'
            if 'introspect' in args:
                return 0, 'org.freedesktop.portal.RemoteDesktop ConnectToEIS'
            return 0, ''
        with patch.object(wayland_session, 'socket_error', return_value=None), \
                patch.object(wayland_session.shutil, 'which', return_value='/usr/bin/tool'), \
                patch.object(wayland_session, 'command_result', side_effect=respond), \
                patch.object(bridge, 'inspect', return_value={'errors': ['no EI'], 'warnings': []}):
            report = wayland_session.inspect(self.env)
        self.assertTrue(report['xtest'])
        self.assertTrue(report['remote_desktop_portal'])
        self.assertIn('no EI', report['errors'])


if __name__ == '__main__':
    unittest.main()
