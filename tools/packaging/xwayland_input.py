"""Read-only evidence for Xwayland's XTEST -> native Wayland input bridge.

Inspect ELF dependencies/imports and the matching server's launch configuration.
Do not execute the inspected binary, connect to EIS, or request input permission.
This is capability evidence, never proof of permission or delivered key events.

Sources: Xwayland hw/xwayland/{meson.build,xwayland-xtest.c}; KWin
src/plugins/eis/eisbackend.cpp and src/xwayland/xwaylandlauncher.cpp. Older
KWin uses -enable-ei-portal; newer KWin passes LIBEI_SOCKET to its Xwayland.
"""
import os
from pathlib import Path
import re
import shutil
import stat
import struct


def elf_imports(path):
    """Read ELF64 little-endian dynamic strings without requiring host binutils.

    Use program headers so stripped section headers are also supported. Other
    layouts and static builds are unknown, rather than misreported as missing EI.
    """
    with Path(path).open('rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Not a regular ELF file')
        data = stream.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError('ELF exceeds inspection limit')

    def region(offset, size):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('Truncated ELF')
        return data[offset:offset + size]

    if region(0, 7) != b'\x7fELF\x02\x01\x01':
        raise ValueError('Expected an ELF64 little-endian executable')
    header = struct.unpack('<HHIQQQIHHHHHH', region(16, 48))
    kind, machine, _, _, phoff, _, _, _, entsize, count, *_ = header
    if kind not in (2, 3) or machine != 62 or entsize != 56 or not 0 < count < 65535:
        raise ValueError('Unsupported ELF layout')
    loads, dynamics = [], []
    for index in range(count):
        ptype, _, offset, address, _, size, _, _ = struct.unpack(
            '<IIQQQQQQ', region(phoff + index * entsize, entsize))
        region(offset, size)
        if ptype == 1:
            loads.append((address, offset, size))
        elif ptype == 2:
            dynamics.append((offset, size))
    if len(dynamics) != 1:
        raise ValueError('No unique dynamic table; static/custom build is unverified')
    offset, size = dynamics[0]
    tags, needed = {}, []
    for pos in range(offset, offset + size - 15, 16):
        tag, value = struct.unpack('<qQ', region(pos, 16))
        if tag == 0:
            break
        if tag == 1:
            needed.append(value)
        tags[tag] = value
    else:
        raise ValueError('Unterminated ELF dynamic table')
    if 5 not in tags or 10 not in tags:
        raise ValueError('Missing ELF dynamic strings')
    address, length = tags[5], tags[10]
    locations = [offset + address - start for start, offset, size in loads
                 if start <= address and address + length <= start + size]
    if len(locations) != 1:
        raise ValueError('Invalid ELF string table mapping')
    strings = region(locations[0], length)
    libraries = []
    for index in needed:
        end = strings.find(b'\0', index)
        if index >= length or end < 0:
            raise ValueError('Invalid ELF dependency name')
        libraries.append(strings[index:end].decode('ascii'))
    return libraries, set(strings.split(b'\0'))


def inspect_binary(path):
    report = {'path': str(path), 'libei_linked': None, 'liboeffis_linked': None,
              'ei_bridge': None, 'portal_bridge': None}
    try:
        libraries, names = elf_imports(path)
    except (OSError, ValueError) as error:
        report['unverified_reason'] = str(error)
        return report
    report['libei_linked'] = any(re.fullmatch(r'libei\.so(?:\.\d+)*', n) for n in libraries)
    report['liboeffis_linked'] = any(re.fullmatch(r'liboeffis\.so(?:\.\d+)*', n) for n in libraries)
    # Linkage AND bridge-specific imports, rather than installed package names
    # or strings accidentally embedded in debug/help text.
    ei_symbols = {b'ei_device_keyboard_key', b'ei_setup_backend_socket'} <= names
    portal_symbol = b'oeffis_create_session' in names
    report['ei_bridge'] = report['libei_linked'] and ei_symbols
    report['portal_bridge'] = report['ei_bridge'] and report['liboeffis_linked'] and portal_symbol
    if (report['libei_linked'] != ei_symbols or
            report['liboeffis_linked'] != portal_symbol):
        # Mixed static/shared linkage or a changed implementation needs review.
        report['ei_bridge'] = report['portal_bridge'] = None
        report['unverified_reason'] = 'Nonstandard bridge linkage/imports; manual inspection needed'
    return report


def matching_servers(env, proc_root):
    """Match the KDE server's explicit display argument, never the first PID.

    KWin passes :N even when -displayfd is also present. Do not guess for
    displayfd-only launchers, remote displays, or ambiguous process matches.
    """
    display = env.get('DISPLAY', '')
    if not re.fullmatch(r':\d+(?:\.\d+)?', display):
        return []
    display = display.split('.')[0]
    matches = []
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid() or (process / 'comm').read_text().strip() != 'Xwayland':
                continue
            args = (process / 'cmdline').read_bytes().split(b'\0')
            if len(args) > 1 and args[1] == display.encode():
                matches.append((process, args))
        except OSError:
            continue
    return matches


def socket_route(process):
    # Read only these two values. Never put the server's whole environment in
    # a support log. /proc/self/fd refers to Xwayland, not this Python process.
    values = dict(item.split(b'=', 1) for item in (process / 'environ').read_bytes().split(b'\0')
                  if item.startswith((b'LIBEI_SOCKET=', b'XDG_RUNTIME_DIR=')))
    name = os.fsdecode(values.get(b'LIBEI_SOCKET', b''))
    if not name:
        return False
    if re.fullmatch(r'/proc/self/fd/\d+', name):
        path = process / 'fd' / name.rsplit('/', 1)[1]
    elif name.startswith('/proc/'):
        return None
    else:
        path = Path(name)
        if not path.is_absolute():
            runtime = Path(os.fsdecode(values.get(b'XDG_RUNTIME_DIR', b'')))
            if not runtime.is_absolute():
                return None
            path = runtime / path
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    return stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()


def inspect(env, proc_root=Path('/proc')):
    report = {'errors': [], 'warnings': [], 'server_identified': False,
              'route': 'unverified', 'configuration_supported': None,
              'permission_granted': 'not measured', 'key_delivery_tested': False}
    try:
        matches = matching_servers(env, proc_root)
    except OSError:
        matches = []
    if len(matches) != 1:
        installed = shutil.which('Xwayland', path=env.get('PATH'))
        if installed:
            report['installed_binary'] = inspect_binary(installed)
        report['warnings'].append('Cannot uniquely identify this display\'s running Xwayland. '
                                  'Installed-binary evidence does not verify the active native-input bridge.')
        return report
    process, args = matches[0]
    report.update(server_identified=True, pid=int(process.name))
    binary = report['binary'] = inspect_binary(process / 'exe')
    if binary['ei_bridge'] is False:
        report['configuration_supported'] = False
        report['errors'].append('The running Xwayland has no detectable linked libei keyboard bridge. XTEST and a portal alone '
                                'cannot deliver bindings to native Wayland apps. A distribution Xwayland build '
                                'with EI support is required; installing libei alone does not add compiled support.')
        return report
    if binary['ei_bridge'] is None:
        report['warnings'].append('Cannot verify the running Xwayland input bridge: ' + binary['unverified_reason'])
        return report
    if b'-enable-ei-portal' in args and binary['portal_bridge']:
        report.update(route='portal', configuration_supported=True)
        return report
    try:
        direct = socket_route(process)
    except OSError as error:
        direct = None
        report['warnings'].append('Cannot inspect Xwayland\'s private EIS socket: ' + str(error))
    if direct:
        report.update(route='private-eis-socket', configuration_supported=True)
    elif direct is None:
        report['warnings'].append('The native-input route is unverified; no input permission was requested.')
    else:
        report['configuration_supported'] = False
        report['errors'].append('Xwayland includes libei, but neither an enabled EI/OEFFIS portal route nor '
                                'a valid private EIS socket was found. Check the Plasma/KWin session setup; '
                                'do not bypass input permission controls.')
    return report
