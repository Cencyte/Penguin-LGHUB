#!/usr/bin/env bash
# Matched experimental ntdll + wineserver build; never runs make install.
# Usage: build_private_ntdll.sh SOURCE BUILD [--alpc|--alpc-messages|--alpc-wnf-probe]
# Use a dedicated source/build pair, separate from build_private_wine.sh.
set -euo pipefail
src=$(realpath -e -- "${1:?Provide the Wine 10.20 source directory}")
out=${2:?Provide a separate build directory}
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
[[ $(cat "$src/VERSION") == 'Wine version 10.20' ]] || {
    echo 'These patches require Wine 10.20 source.' >&2; exit 2;
}
patches=(0003-wait-completion-packets.patch)
case ${3:-} in
    '') ;;
    --alpc) patches+=(0004-alpc-ports-and-attributes.patch) ;;
    --alpc-messages) patches+=(0004-alpc-ports-and-attributes.patch 0005-alpc-message-experiment.patch) ;;
    --alpc-wnf-probe) patches+=(0004-alpc-ports-and-attributes.patch 0005-alpc-message-experiment.patch
        0011-wnf-subscription-failure.patch) ;;
    *) echo 'Unknown option; expected --alpc, --alpc-messages or --alpc-wnf-probe.' >&2; exit 2 ;;
esac
mkdir -p -- "$out"
out=$(realpath -e -- "$out")
[[ "$src" != "$out" ]] || { echo 'Use an out-of-source build directory.' >&2; exit 2; }
patch_files=()
for name in "${patches[@]}"; do patch_files+=("$repo/patches/wine/$name"); done
python3 "$repo/tools/wine/apply_patch_series.py" "$src" "${patch_files[@]}"
cd -- "$src"
./tools/make_requests
./tools/make_specfiles
cd -- "$out"
# New headers added by a later patch also require re-running configure/makedep.
if [[ ! -f Makefile || "$src/include/Makefile.in" -nt Makefile ]]; then
    "$src/configure" --prefix=/usr --libdir=/usr/lib --enable-win64 \
        --without-x --without-wayland --without-vulkan --without-opengl --disable-tests
fi
make -j2 dlls/ntdll/x86_64-windows/ntdll.dll dlls/ntdll/ntdll.so server/wineserver
if [[ ${3:-} == --alpc-wnf-probe ]]; then
    x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
        "$repo/tools/wine/wnf_unavailable_test.c" -o "$out/wnf_unavailable_test.exe"
fi
