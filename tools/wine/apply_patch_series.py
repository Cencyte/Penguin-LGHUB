#!/usr/bin/env python3
"""Apply an overlapping Wine patch series, preflighting in a temporary copy.

Checking each patch backwards directly in the final source fails when a later
patch changed the same lines. Undo candidate prefixes in reverse order in the
copy instead. Supports a pristine source, a complete series and an extension of
an already-applied prefix. Rejects conflicts before writing source files.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def apply(directory, patch_file, reverse=False):
    with patch_file.open('rb') as stream:
        return subprocess.run(
            ['patch', '--batch', '--force', '--fuzz=0', '--no-backup-if-mismatch',
             '-p1', '-d', str(directory), *(['-R'] if reverse else [])],
            stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def apply_series(source, patches):
    paths = set()
    for patch in patches:
        for line in patch.read_text().splitlines():
            if line.startswith(('--- a/', '+++ b/')):
                path = Path(line[6:].split('\t', 1)[0])
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError(f'Unsafe patch path: {path}')
                paths.add(path)
    if not paths:
        raise ValueError('No patch paths found')
    failure = b''
    for count in range(len(patches), -1, -1):
        with tempfile.TemporaryDirectory(prefix='ghub-patch-check-') as directory:
            scratch = Path(directory)
            for relative in paths:
                current = source / relative
                if current.is_file():
                    destination = scratch / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(current, destination)
            for patch in reversed(patches[:count]):
                result = apply(scratch, patch, reverse=True)
                if result.returncode:
                    break
            else:
                # Rebuild the entire requested series in the scratch copy.
                for patch in patches:
                    result = apply(scratch, patch)
                    if result.returncode:
                        failure = result.stdout
                        break
                else:
                    for patch in patches[:count]:
                        print(f'Already applied: {patch.name}')
                    for patch in patches[count:]:
                        result = apply(source, patch)
                        print(result.stdout.decode(errors='replace'), end='')
                        if result.returncode:
                            raise RuntimeError('Source changed after preflight; stop and inspect it')
                    return
    raise RuntimeError('Patch series conflicts; source left unchanged.\n' + failure.decode(errors='replace'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('patches', nargs='+', type=Path)
    args = parser.parse_args()
    apply_series(args.source.resolve(strict=True), [p.resolve(strict=True) for p in args.patches])
