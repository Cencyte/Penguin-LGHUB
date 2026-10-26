#!/usr/bin/env bash
# Usage: build_private_wine.sh WINE_10_20_SOURCE BUILD_DIRECTORY
# Applies the two local patches and builds only the three needed 64-bit DLLs.
# Uses existing build dependencies, limits memory with -j2, never runs make install.
set -euo pipefail
src=$(realpath -e -- "${1:?Provide the Wine 10.20 source directory}")
out=${2:?Provide a separate build directory}
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
[[ $(cat "$src/VERSION") == 'Wine version 10.20' ]] || {
    echo 'These patches require Wine 10.20 source.' >&2
    exit 2
}
mkdir -p -- "$out"
out=$(realpath -e -- "$out")
[[ "$src" != "$out" ]] || { echo 'Use an out-of-source build directory.' >&2; exit 2; }
for name in 0001-findfirst-share-delete.patch 0002-device-parent-relationships.patch; do
    patch_file=$repo/patches/wine/$name
    if patch --batch --dry-run -R -d "$src" -p1 < "$patch_file" >/dev/null 2>&1; then
        echo "Already applied: $name"
    else
        patch --batch --forward -d "$src" -p1 < "$patch_file"
    fi
done
cd -- "$out"
if [[ ! -f Makefile ]]; then
    "$src/configure" --enable-win64 --without-x --without-wayland \
        --without-vulkan --without-opengl --disable-tests
fi
make -j2 dlls/setupapi/x86_64-windows/setupapi.dll \
    dlls/ntoskrnl.exe/x86_64-windows/ntoskrnl.exe \
    dlls/kernelbase/x86_64-windows/kernelbase.dll
