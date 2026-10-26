#!/usr/bin/env bash
# Build only the private WinRT timer module and its independent ABI regression.
set -euo pipefail
src=$(realpath -e -- "${1:?Provide a separate Wine 10.20 source directory}")
out=${2:?Provide a separate build directory}
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
[[ $(cat "$src/VERSION") == 'Wine version 10.20' ]] || { echo 'Wine 10.20 required.' >&2; exit 2; }
mkdir -p -- "$out"
out=$(realpath -e -- "$out")
[[ "$src" != "$out" ]] || { echo 'Use an out-of-source build directory.' >&2; exit 2; }
python3 "$repo/tools/wine/apply_patch_series.py" "$src" "$repo/patches/wine/0030-threadpoolwinrt-timer.patch"
cd -- "$out"
if [[ ! -f Makefile ]]; then
    "$src/configure" --prefix=/usr --libdir=/usr/lib --enable-win64 --without-wayland --disable-tests
fi
make depend
make -j4 dlls/threadpoolwinrt/x86_64-windows/threadpoolwinrt.dll
x86_64-w64-mingw32-gcc -O2 -municode -Wall -Wextra -Werror -Wno-cast-function-type \
    "$repo/tools/wine/threadpool_timer_test.c" -o "$out/threadpool_timer_test.exe" \
    "$out/dlls/combase/x86_64-windows/libcombase.a" "$out/libs/uuid/x86_64-windows/libuuid.a"
printf '%s\n' 'Wine 10.20 patch0030; timer lifetime and WinRT ABI v1' > ghub-threadpool-timer-protocol
