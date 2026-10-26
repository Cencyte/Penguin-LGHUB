#!/usr/bin/env bash
# Add the pointer-transform export to an existing matched input-routing build.
set -euo pipefail
src=$(realpath -e -- "${1:?Wine 10.20 source directory}")
out=$(realpath -e -- "${2:?Configured matched build directory}")
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
[[ $(cat "$src/VERSION") == 'Wine version 10.20' && -f "$out/ghub-delegation-protocol" ]]
patches=("$repo/patches/wine/0036-pointer-input-transform-fallback.patch")
if [[ -f "$out/ghub-native-input-protocol" ]]; then
    for name in 0040-composition-mouse-pointer 0041-native-input-compatibility 0042-dwm-contact-feedback; do
        patches+=("$repo/patches/wine/$name.patch")
    done
fi
python3 "$repo/tools/wine/apply_patch_series.py" "$src" "${patches[@]}"
make -C "$out" -j4 dlls/user32/x86_64-windows/user32.dll
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror -Wno-cast-function-type \
    "$repo/tools/wine/tray_pointer_test.c" -o "$out/tray_pointer_test.exe"
x86_64-w64-mingw32-g++ -std=c++17 -O2 -static -Wall -Wextra -Werror \
    "$repo/tools/wine/tray_anchor_test.cpp" -lcomctl32 -o "$out/tray_anchor_test.exe"
