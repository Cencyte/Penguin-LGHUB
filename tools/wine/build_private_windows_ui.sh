#!/usr/bin/env bash
# Backport CoreWindow/UISettings and the gated composition InputSite bridge.
# No package changes, downloads or make install. Uses existing build dependencies.
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
    "$repo/patches/wine/0012-corewindow-desktop-query.patch" \
    "$repo/patches/wine/0021-uisettings-animations.patch" \
    "$repo/patches/wine/0026-inputsite-sink-identity.patch" \
    "$repo/patches/wine/0027-activation-configuration.patch"
cd -- "$out"
if [[ ! -f Makefile ]]; then
    "$src/configure" --prefix=/usr --libdir=/usr/lib --enable-win64 \
        --without-x --without-wayland --without-vulkan --without-opengl --disable-tests
fi
make -j2 dlls/windows.ui/x86_64-windows/windows.ui.dll
x86_64-w64-mingw32-gcc -std=c11 -O2 -Wall -Wextra -Werror -Wno-cast-function-type -static \
    "$repo/tools/wine/corewindow_test.c" -lruntimeobject -lole32 -luuid \
    -o "$out/corewindow_test.exe"
x86_64-w64-mingw32-gcc -std=c11 -O2 -Wall -Wextra -Werror -static \
    "$repo/tools/wine/uisettings_test.c" -lruntimeobject -lole32 -luuid -luser32 \
    -o "$out/uisettings_test.exe"
x86_64-w64-mingw32-g++ -std=c++17 -O2 -Wall -Wextra -Werror -Wno-cast-function-type -static \
    "$repo/tools/wine/inputsite_test.cpp" -lruntimeobject -lole32 -luuid \
    -o "$out/inputsite_test.exe"
x86_64-w64-mingw32-g++ -std=c++17 -O2 -Wall -Wextra -Werror -Wno-cast-function-type -static \
    "$repo/tools/wine/activation_config_test.cpp" -lruntimeobject -lole32 -luuid \
    -o "$out/activation_config_test.exe"
