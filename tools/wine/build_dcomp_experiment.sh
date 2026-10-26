#!/usr/bin/env bash
# Builds the isolated prototype DLL; does not deploy to any prefix.
set -euo pipefail
src=$(cd -- "$(dirname -- "$0")" && pwd)
out=${1:?Provide a lab build directory}
mkdir -p -- "$out"
x86_64-w64-mingw32-g++ -std=c++17 -shared -O2 -Wall -Wextra -Werror \
    -static \
    "$src/dcomp_experiment.cpp" "$src/dcomp_clock.cpp" "$src/dcomp_experiment.def" \
    -lole32 -lgdi32 -luser32 -ld3d11 -ldxguid -luuid -lntdll -o "$out/dcomp.dll"
bash "$src/build_dcomp_device_probe.sh" "$out"
x86_64-w64-mingw32-g++ -std=c++17 -O2 -Wall -Wextra -Werror -Wno-cast-function-type -static \
    "$src/dcomp_clock_test.cpp" -luser32 -o "$out/dcomp_clock_test.exe"
x86_64-w64-mingw32-g++ -std=c++17 -O2 -Wall -Wextra -Werror -static \
    "$src/dcomp_render_test.cpp" -lole32 -lgdi32 -luser32 -ld3d11 -ld2d1 -ldxguid -luuid \
    -o "$out/dcomp_render_test.exe"
