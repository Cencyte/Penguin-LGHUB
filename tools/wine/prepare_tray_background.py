#!/usr/bin/env python3
"""Build version-pinned G HUB tray resources with a solid acrylic fallback.

Add only bgImage.Opacity=0; retain its visibility, dimensions, stretch, margin
and load path. The native EXE remains unchanged. The image normally contains a
frozen desktop capture that becomes visible beside the menu in Wine.

Formats: Microsoft.UI.Xaml ObjectWriterNodeType.h/StableXbfPropertyIndexes.g.csv
and WindowsAppSDK MRTCore FileBase.h/FileBaseSections.h. This is deliberately
not a general-purpose PRI editor. No proprietary payloads are embedded here.
"""
import argparse
import hashlib
from pathlib import Path
import struct

XBF_SHA256 = 'adfac6b993eedbe3f60135b587dadbc5de103d4bf6b1b670c1e64652bf238ae7'
PRI_SHA256 = 'fe11582f6cb41dc85f31d394c465a20b5ce8d2a7c316ca354307b410ceb8f3f5'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def u32(blob, offset):
    return struct.unpack_from('<I', blob, offset)[0]


def put32(blob, offset, value):
    struct.pack_into('<I', blob, offset, value)


def sections(blob):
    base, toc = u32(blob, 20), u32(blob, 16)
    for index in range(struct.unpack_from('<H', blob, 24)[0]):
        entry = toc + index * 32
        yield entry, base + u32(blob, entry + 24), u32(blob, entry + 28)


def data_items(blob, entry, start, size):
    if blob[entry:entry+16].rstrip(b'\0 ') != b'[mrm_dataitem]':
        return []
    require(u32(blob, start+24) == size, 'PRI section header size')
    require(struct.unpack_from('<II', blob, start+size-8) == (0xdef5fade, size), 'PRI trailer')
    flags, small, large, length = struct.unpack_from('<IHHI', blob, start+32)
    require(flags == 0, 'Unsupported data item flags')
    table = start+44
    data = table + small*4 + large*8
    result = []
    for index in range(small+large):
        fmt = '<HH' if index < small else '<II'
        offset, count = struct.unpack_from(fmt, blob, table)
        require(offset+count <= length, 'Item exceeds data section')
        result.append((table, fmt, offset, count, data))
        table += struct.calcsize(fmt)
    return result


def payloads(blob):
    return [bytes(blob[data+offset:data+offset+length])
            for entry, start, size in sections(blob)
            for _, _, offset, length, data in data_items(blob, entry, start, size)]


def patch_resources(original_xbf, original_pri):
    for name, data, expected in [('MainWindow.xbf', original_xbf, XBF_SHA256),
                                  ('resources.pri', original_pri, PRI_SHA256)]:
        require(hashlib.sha256(data).hexdigest() == expected,
                'Unsupported or already modified '+name)

    # Insert SetValueConstant(UIElement_Opacity, Float, 0) immediately before
    # the existing Image_Stretch=None node, without changing the scope or layout.
    marker = bytes.fromhex('1a4d820b990200000000')
    require(original_xbf.count(marker) == 1, 'Ambiguous image node')
    at = original_xbf.index(marker)
    xbf = bytearray(original_xbf[:at] + bytes.fromhex('1a2f810300000000') + original_xbf[at:])
    meta, nodes = struct.unpack_from('<II', xbf, 4)
    substream = 12+meta
    require(struct.unpack_from('<III', xbf, substream) == (1, 0, nodes-12), 'Unsupported XBF substreams')
    put32(xbf, 8, nodes+8)
    put32(xbf, substream+8, nodes-4)

    matches = []
    for entry, start, size in sections(original_pri):
        items = data_items(original_pri, entry, start, size)
        for table, fmt, offset, length, data in items:
            if original_pri[data+offset:data+offset+length] == original_xbf:
                matches.append((entry, start, size, items, table, offset, length, data))
    require(len(matches) == 1, 'MainWindow resource must occur exactly once')
    entry, start, size, items, selected, offset, length, data = matches[0]
    pri = bytearray(original_pri)
    delta = len(xbf)-length
    require(delta == 8, 'Unexpected XBF size change')
    pri[data+offset:data+offset+length] = xbf
    for table, fmt, item_offset, item_length, _ in items:
        if table == selected:
            item_length += delta
        elif item_offset >= offset+length:
            item_offset += delta
        struct.pack_into(fmt, pri, table, item_offset, item_length)
    put32(pri, start+40, u32(pri, start+40)+delta)
    put32(pri, start+24, size+delta)
    put32(pri, start+size+delta-4, size+delta)
    put32(pri, entry+28, size+delta)
    for other, other_start, _ in sections(original_pri):
        if other_start > start:
            put32(pri, other+24, u32(pri, other+24)+delta)
    put32(pri, 12, len(pri))
    put32(pri, len(pri)-12, len(pri))

    before, after = payloads(original_pri), payloads(pri)
    require(len(before) == len(after) == 8109, 'Resource count changed')
    require(sum(a != b for a, b in zip(before, after)) == 1, 'Unexpected changed resources')
    require(all(a == b or (a == original_xbf and b == xbf) for a, b in zip(before, after)),
            'Unrelated resource payload changed')
    return bytes(xbf), bytes(pri)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Original system_tray directory')
    parser.add_argument('--output', required=True, type=Path, help='New directory; never overwrite inputs')
    args = parser.parse_args()
    xbf, pri = patch_resources((args.source/'MainWindow.xbf').read_bytes(),
                               (args.source/'resources.pri').read_bytes())
    args.output.mkdir(parents=True, exist_ok=False)
    for name, data in [('MainWindow.xbf', xbf), ('resources.pri', pri)]:
        (args.output/name).write_bytes(data)
        print(hashlib.sha256(data).hexdigest(), args.output/name)


if __name__ == '__main__':
    main()
