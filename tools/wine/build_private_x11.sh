#!/usr/bin/env bash
# Rebuild only the X11 driver in an existing configured Wine 10.20 tree.
set -euo pipefail
src=$(realpath -e -- "${1:?Provide a Wine 10.20 source directory}")
out=$(realpath -e -- "${2:?Provide its configured build directory}")
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
[[ $(cat "$src/VERSION") == 'Wine version 10.20' && -f "$out/Makefile" ]]
python3 "$repo/tools/wine/apply_patch_series.py" "$src" \
    "$repo/patches/wine/0033-x11-ignore-released-mouse-moveresize.patch" \
    "$repo/patches/wine/0035-x11-flush-direct-image-upload.patch" \
    "$repo/patches/wine/0039-x11-layered-surface-format.patch"
make -C "$out" -j4 dlls/winex11.drv/x86_64-windows/winex11.drv dlls/winex11.drv/winex11.so
x86_64-w64-mingw32-gcc -O2 -municode -Wall -Wextra -Werror \
    "$repo/tools/wine/window_command_probe.c" -o "$out/window_command_probe.exe"
