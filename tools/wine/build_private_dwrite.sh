#!/usr/bin/env bash
# Build the private text analyzer/factory and ABI/render tests without installing Wine.
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
    "$repo/patches/wine/0020-private-text-analyzer.patch" \
    "$repo/patches/wine/0022-private-dwrite-factory.patch"
cd -- "$out"
if [[ ! -f Makefile ]]; then
    "$src/configure" --prefix=/usr --libdir=/usr/lib --enable-win64 \
        --without-x --without-wayland --without-vulkan --without-opengl
fi
make -j2 dlls/dwrite/x86_64-windows/dwrite.dll
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
    "$repo/tools/wine/text_reading_direction_test.c" -luuid \
    -o "$out/text_reading_direction_test.exe"
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
    "$repo/tools/wine/private_dwrite_test.c" -luuid \
    -o "$out/private_dwrite_test.exe"
x86_64-w64-mingw32-g++ -O2 -Wall -Wextra -Werror \
    "$repo/tools/wine/dwrite_d2d_render_test.cpp" -ld3d11 -ld2d1 -ldwrite -lole32 -luuid -static \
    -o "$out/dwrite_d2d_render_test.exe"
