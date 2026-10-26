#!/usr/bin/env bash
# Build private Wine 10.20 D3D11 and optionally WineD3D DLLs. Never runs make install.
# --d2d-private-info includes guarded 2D rendering; other private partner calls
# remain diagnostic. D3D11 and WineD3D from that build must be used together.
# --shared-textures also builds matching DXGI and the opt-in CPU texture transport.
# --enqueue-event adds GPU completion events to that matching trio.
set -euo pipefail
src=$(realpath -e -- "${1:?Provide the Wine 10.20 source directory}")
out=${2:?Provide a separate build directory}
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
[[ $(cat "$src/VERSION") == 'Wine version 10.20' ]] || {
    echo 'These patches require Wine 10.20 source.' >&2; exit 2;
}
patches=("$repo/patches/wine/0006-d3d11-creation-flags.patch")
case ${3:-} in
    '') ;;
    --partner-probe) patches+=("$repo/patches/wine/0007-d3d11-partner-diagnostic.patch") ;;
    --d2d-private-info|--shared-textures|--enqueue-event) patches+=("$repo/patches/wine/0007-d3d11-partner-diagnostic.patch"
                                "$repo/patches/wine/0008-d2d-private-flush-counter.patch") ;;
    *) echo 'Unknown option; expected --partner-probe, --d2d-private-info, --shared-textures or --enqueue-event.' >&2; exit 2 ;;
esac
patches+=("$repo/patches/wine/0009-d3d11-immediate-context.patch"
         "$repo/patches/wine/0010-d3d11-shader-resource-view1.patch")
if [[ ${3:-} == --d2d-private-info || ${3:-} == --shared-textures || ${3:-} == --enqueue-event ]]; then
    patches+=("$repo/patches/wine/0023-d3d11-guard-rectangles.patch")
fi
if [[ ${3:-} == --shared-textures || ${3:-} == --enqueue-event ]]; then
    patches+=("$repo/patches/wine/0024-d3d11-shared-textures.patch")
fi
if [[ ${3:-} == --enqueue-event ]]; then
    patches+=("$repo/patches/wine/0025-dxgi-enqueue-set-event.patch")
fi
patches+=("$repo/patches/wine/0029-wined3d-backup-context-format.patch")
if [[ ${3:-} == --shared-textures || ${3:-} == --enqueue-event ]]; then
    patches+=("$repo/patches/wine/0031-dxgi-adapter-notifications.patch")
fi
mkdir -p -- "$out"
out=$(realpath -e -- "$out")
[[ "$src" != "$out" ]] || { echo 'Use an out-of-source build directory.' >&2; exit 2; }
python3 "$repo/tools/wine/apply_patch_series.py" "$src" "${patches[@]}"
cd -- "$out"
if [[ ! -f Makefile ]]; then
    "$src/configure" --prefix=/usr --libdir=/usr/lib --enable-win64 \
        --without-x --without-wayland --without-vulkan --without-opengl --disable-tests
fi
make -j2 dlls/d3d11/x86_64-windows/d3d11.dll
if [[ ${3:-} == --d2d-private-info || ${3:-} == --shared-textures || ${3:-} == --enqueue-event ]]; then
    make -j2 dlls/wined3d/x86_64-windows/wined3d.dll
    x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
        "$repo/tools/wine/d2d_private_info_test.c" -ld3d11 -ldxguid \
        -o "$out/d2d_private_info_test.exe"
    x86_64-w64-mingw32-g++ -O2 -Wall -Wextra -Werror -static \
        "$repo/tools/wine/d3d11_guard_rect_test.cpp" -ld3d11 -ld3dcompiler -ld2d1 -lole32 -luuid \
        -o "$out/d3d11_guard_rect_test.exe"
fi
if [[ ${3:-} == --shared-textures || ${3:-} == --enqueue-event ]]; then
    make -j2 dlls/dxgi/x86_64-windows/dxgi.dll
    x86_64-w64-mingw32-g++ -std=c++17 -O2 -Wall -Wextra -Werror -static \
        "$repo/tools/wine/dxgi_adapter_notifications_test.cpp" -ldxgi \
        -o "$out/dxgi_adapter_notifications_test.exe"
    x86_64-w64-mingw32-g++ -O2 -Wall -Wextra -Werror -static \
        "$repo/tools/wine/d3d11_shared_texture_test.cpp" -ld3d11 -ldxgi -ld3dcompiler \
        -o "$out/d3d11_shared_texture_test.exe"
fi
if [[ ${3:-} == --enqueue-event ]]; then
    x86_64-w64-mingw32-g++ -O2 -Wall -Wextra -Werror -static \
        "$repo/tools/wine/dxgi_enqueue_event_test.cpp" -ld3d11 -ldxgi \
        -o "$out/dxgi_enqueue_event_test.exe"
fi
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
    "$repo/tools/wine/d3d11_creation_flags_test.c" -ld3d11 -ldxguid \
    -o "$out/d3d11_creation_flags_test.exe"
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
    "$repo/tools/wine/d3d11_srv1_test.c" -ld3d11 -ldxguid \
    -o "$out/d3d11_srv1_test.exe"
# For native-D2D validation, place the user's Windows d2d1.dll beside this test
# in a separate lab directory and run with d2d1=n and the partner probe enabled.
x86_64-w64-mingw32-g++ -O2 -Wall -Wextra -Werror -static-libgcc -static-libstdc++ \
    "$repo/tools/wine/d2d_native_render_test.cpp" -ld3d11 -ld2d1 -ldxguid \
    -o "$out/d2d_native_render_test.exe"
