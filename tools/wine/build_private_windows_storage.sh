#!/usr/bin/env bash
# Build memory streams and DataWriter with ABI tests without installing Wine.
set -euo pipefail
src=$(realpath -e -- "${1:?Provide a separate Wine 10.20 source directory}")
out=${2:?Provide a separate build directory}
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
[[ $(cat "$src/VERSION") == 'Wine version 10.20' ]] || {
    echo 'This patch requires Wine 10.20 source.' >&2; exit 2;
}
mkdir -p -- "$out"
out=$(realpath -e -- "$out")
[[ "$src" != "$out" ]] || { echo 'Use an out-of-source build directory.' >&2; exit 2; }
python3 "$repo/tools/wine/apply_patch_series.py" "$src" \
    "$repo/patches/wine/0018-memory-stream.patch" \
    "$repo/patches/wine/0019-data-writer.patch"
cd -- "$out"
if [[ ! -f Makefile ]]; then
    "$src/configure" --prefix=/usr --libdir=/usr/lib --enable-win64 \
        --without-x --without-wayland --without-vulkan --without-opengl
fi
# Build the test executable only; run it on private Xvfb with the test runner.
make -j2 dlls/windows.storage/x86_64-windows/windows.storage.dll \
    dlls/windows.storage/tests/x86_64-windows/windows.storage_test.exe
