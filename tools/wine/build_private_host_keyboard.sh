#!/usr/bin/env bash
# Add opt-in G HUB keyboard output to the matched private Wine 10.20 build.
set -euo pipefail
src=$(realpath -e -- "${1:?Wine 10.20 source directory}")
out=$(realpath -e -- "${2:?Configured matched build directory}")
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
[[ $(cat "$src/VERSION") == 'Wine version 10.20' && -f "$out/ghub-composition-protocol" ]]
python3 "$repo/tools/wine/apply_patch_series.py" "$src" \
    "$repo/patches/wine/0043-ghub-host-keyboard-output.patch"
make -C "$out" -j4 dlls/win32u/win32u.so dlls/winex11.drv/winex11.so
printf '%s\n' 'ghub-host-keyboard-v1' > "$out/ghub-host-keyboard-protocol"
