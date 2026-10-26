#!/usr/bin/env bash
# Opt-in retained layered-child composition in a private Wine 10.20 Win32u.
# Uses existing graphics dependencies; no downloads, installation or upgrades.
# Pass --input-sink to build the matched Win32u/ntdll/server protocol extension.
# --input-routing adds immutable shared input scenes and hardware routing.
# --input-delegation additionally builds User32 for delegated input callbacks.
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
if [[ -f "$out/ghub-input-routing-protocol" && ${3:-} != --input-routing ]]; then
    echo 'This build includes input routing; use --input-routing.' >&2; exit 2
fi
patches=(0013-retained-child-composition.patch 0016-window-bound-composition.patch)
case ${3:-} in
    '')
        [[ ! -f "$src/server/composition.c" ]] || {
            echo 'This source includes the input-sink protocol; use --input-sink.' >&2; exit 2;
        } ;;
    --input-sink|--input-delegation|--input-routing)
        patches=(0003-wait-completion-packets.patch 0004-alpc-ports-and-attributes.patch
            0005-alpc-message-experiment.patch 0011-wnf-subscription-failure.patch
            0013-retained-child-composition.patch 0014-composition-input-sink.patch)
        if [[ ${3:-} == --input-delegation || ${3:-} == --input-routing ]]; then
            patches+=(0015-delegated-spatial-input.patch)
        elif [[ -f "$out/ghub-delegation-protocol" ]]; then
            echo 'This build includes delegated input; use --input-delegation.' >&2; exit 2
        fi ;;
    *) echo 'Unknown option; expected --input-sink, --input-delegation or --input-routing.' >&2; exit 2 ;;
esac
if [[ -n ${3:-} ]]; then patches+=(0016-window-bound-composition.patch); fi
if [[ ${3:-} == --input-delegation || ${3:-} == --input-routing ]]; then patches+=(0017-window-destruction-state.patch); fi
if [[ ${3:-} == --input-routing ]]; then patches+=(0028-composition-input-routing.patch); fi
patches+=(0034-lazy-retained-window-composition.patch)
if [[ ${3:-} == --input-routing ]]; then patches+=(0036-pointer-input-transform-fallback.patch 0037-composition-host-cursor.patch); fi
patches+=(0038-composition-transparent-host.patch)
if [[ ${3:-} == --input-routing ]]; then
    patches+=(0040-composition-mouse-pointer.patch 0041-native-input-compatibility.patch 0042-dwm-contact-feedback.patch)
fi
patch_files=()
for name in "${patches[@]}"; do patch_files+=("$repo/patches/wine/$name"); done
python3 "$repo/tools/wine/apply_patch_series.py" "$src" "${patch_files[@]}"
if [[ -n ${3:-} ]]; then
    cd -- "$src"
    ./tools/make_requests
    ./tools/make_specfiles
fi
cd -- "$out"
if [[ ! -f Makefile ]]; then
    "$src/configure" --prefix=/usr --libdir=/usr/lib --enable-win64 --without-wayland --disable-tests
fi
# The new included header must participate in incremental dependency tracking.
make depend
make -j2 dlls/win32u/x86_64-windows/win32u.dll dlls/win32u/win32u.so
if [[ -n ${3:-} ]]; then
    make -j2 dlls/ntdll/x86_64-windows/ntdll.dll dlls/ntdll/ntdll.so server/wineserver
    x86_64-w64-mingw32-gcc -O2 -municode -Wall -Wextra -Werror \
        -Wno-cast-function-type -Wno-missing-field-initializers \
        "$repo/tools/wine/composition_input_test.c" -o "$out/composition_input_test.exe"
    # Runner refuses to mount this Win32u without its matching server pair.
    printf '%s\n' 'Wine 10.20 patches 0003,0004,0005,0011,0013,0014' > ghub-composition-protocol
fi
if [[ ${3:-} == --input-delegation || ${3:-} == --input-routing ]]; then
    make -j2 dlls/user32/x86_64-windows/user32.dll
    x86_64-w64-mingw32-gcc -O2 -municode -Wall -Wextra -Werror -Wno-cast-function-type \
        "$repo/tools/wine/delegated_input_test.c" -o "$out/delegated_input_test.exe"
    x86_64-w64-mingw32-gcc -O2 -municode -Wall -Wextra -Werror -Wno-cast-function-type \
        "$repo/tools/wine/window_destroy_test.c" -o "$out/window_destroy_test.exe"
    printf '%s\n' 'Wine 10.20 patches0015-0017; matched User32 callbacks and window state required' > ghub-delegation-protocol
fi

if [[ ${3:-} == --input-routing ]]; then
    make -j2 dlls/dwmapi/x86_64-windows/dwmapi.dll
    printf '%s\n' 'Wine 10.20 patches0040-0042; opt-in composition mouse and native ninput compatibility' > ghub-native-input-protocol
    printf '%s\n' 'Wine 10.20 patch0028; code29 scene ABI47484931; matched Win32u/ntdll/server required' > ghub-input-routing-protocol
fi
