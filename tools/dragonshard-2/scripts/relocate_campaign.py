#!/usr/bin/env python3
"""Verified incremental relocation of a quiescent campaign, preserving explicitly held live files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

GIB = 1024**3


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
            os.posix_fadvise(stream.fileno(), 0, stream.tell(), os.POSIX_FADV_DONTNEED)
    return h.hexdigest()


def relocate(source, destination, held_root, holds, units):
    if source.is_symlink() or not source.is_dir() or destination.exists() or held_root.exists():
        raise RuntimeError('Source must be a real directory; destination and hold directory must be new')
    for unit in units:
        state = subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'FreezerState', '--value'], text=True).strip()
        if state != 'frozen':
            raise RuntimeError(f'{unit} must be frozen before relocation')
    destination.mkdir(parents=True)
    held_root.mkdir()
    journal = destination / 'relocation.jsonl'
    entries = list(source.rglob('*'))
    files = [p for p in entries if p.is_file() and not p.is_symlink() and
             not any(p == source / h or (source / h) in p.parents for h in holds)]
    total = sum(p.stat().st_size for p in files)
    if shutil.disk_usage(destination).free < total + 16 * GIB:
        raise RuntimeError('Destination does not fit all files plus the requested disk-full protection')
    projects = {p.parent for p in files if p.suffix == '.gpr'}
    states = {str(p.relative_to(source)): {} for p in projects}
    with journal.open('x') as record:
        def log(value):
            record.write(json.dumps(dict(at=time.time(), **value)) + '\n'); record.flush(); os.fsync(record.fileno())
        log(dict(event='start', source=str(source), destination=str(destination), bytes=total,
                 held_root=str(held_root), holds=[str(p) for p in holds], frozen_units=units))
        for relative in holds:
            original, live, target = source / relative, held_root / relative, destination / relative
            if not original.exists():
                raise RuntimeError(f'Explicit live hold missing: {original}')
            live.parent.mkdir(parents=True, exist_ok=True); target.parent.mkdir(parents=True, exist_ok=True)
            original.rename(live)
            original.symlink_to(live, target_is_directory=live.is_dir())
            target.symlink_to(live, target_is_directory=live.is_dir())
            log(dict(event='held_live_inode', relative=str(relative), location=str(live)))
        moved = 0
        # Free the large closed databases first, rather than retaining a second full source copy.
        for original in sorted(files, key=lambda p: p.stat().st_size, reverse=True):
            before = original.stat(); relative = original.relative_to(source); target = destination / relative
            if shutil.disk_usage(destination).free < before.st_size + 16 * GIB:
                raise RuntimeError('Destination disk gate closed; remaining source files preserved')
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + '.relocating')
            h = hashlib.sha256()
            with original.open('rb') as reader, partial.open('xb') as writer:
                for block in iter(lambda: reader.read(4 * 1024**2), b''):
                    writer.write(block); h.update(block)
                    if writer.tell() % (64 * 1024**2) == 0:
                        writer.flush(); os.fsync(writer.fileno())
                        os.posix_fadvise(writer.fileno(), 0, writer.tell(), os.POSIX_FADV_DONTNEED)
                writer.flush(); os.fsync(writer.fileno())
            after = original.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise RuntimeError(f'Source changed during relocation: {original}')
            expected = h.hexdigest()
            if checksum(partial) != expected:
                raise RuntimeError(f'Copy checksum failed; source retained: {original}')
            shutil.copystat(original, partial)
            partial.rename(target)
            link = original.with_name(original.name + '.relocated-link')
            link.symlink_to(target)
            # The old bytes are released only after fsync and a second, matching SHA256 read.
            os.replace(link, original)
            moved += before.st_size
            for project in projects:
                if original.is_relative_to(project):
                    local = original.relative_to(project)
                    if len(local.parts) == 1 and original.suffix == '.gpr' or len(local.parts) > 1 and local.parts[0].endswith('.rep'):
                        states[str(project.relative_to(source))][str(local)] = dict(size=before.st_size,
                            original_mtime_ns=before.st_mtime_ns, copied_mtime_ns=target.stat().st_mtime_ns, sha256=expected)
            log(dict(event='verified_and_relocated', relative=str(relative), bytes=before.st_size,
                     sha256=expected, moved_bytes=moved, total_bytes=total,
                     source_free_bytes=shutil.disk_usage(source).free, destination_free_bytes=shutil.disk_usage(destination).free))
        # Original symlinks and empty directories are also retained; never follow a live hold.
        for entry in entries:
            relative = entry.relative_to(source)
            if any(entry == source / h or (source / h) in entry.parents for h in holds):
                continue
            target = destination / relative
            if entry.is_symlink() and not target.exists() and not target.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True); target.symlink_to(os.readlink(entry))
            elif entry.is_dir() and not entry.is_symlink():
                target.mkdir(parents=True, exist_ok=True)
        for project, values in states.items():
            (destination / project / '.dragonshard-source-times.json').write_text(json.dumps(values, indent=2) + '\n')
        log(dict(event='all_closed_files_verified', bytes=moved))
        old = source.with_name(source.name + '.relocated-links')
        source.rename(old); source.symlink_to(destination, target_is_directory=True)
        # Only empty directories and relocation links remain here. Refuse any regular data.
        if any(p.is_file() and not p.is_symlink() for p in old.rglob('*')):
            raise RuntimeError('Unexpected remaining regular source file; link tree retained')
        shutil.rmtree(old)
        log(dict(event='complete', bytes=moved, source=str(source), destination=str(destination)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path); ap.add_argument('destination', type=Path); ap.add_argument('held_root', type=Path)
    ap.add_argument('--hold', action='append', type=Path, default=[])
    ap.add_argument('--unit', action='append', default=[])
    args = ap.parse_args()
    for hold in args.hold:
        if hold.is_absolute() or '..' in hold.parts: ap.error('Hold paths must be relative children')
    relocate(args.source.absolute(), args.destination.absolute(), args.held_root.absolute(), args.hold, args.unit)


if __name__ == '__main__':
    main()
