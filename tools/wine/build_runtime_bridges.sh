#!/usr/bin/env bash
# Usage: bash build_runtime_bridges.sh OUTPUT_DIRECTORY
# Uses the existing cross compiler; installs no packages or system files.
set -euo pipefail
src=$(cd -- "$(dirname -- "$0")" && pwd)
out=${1:?Provide an output directory}
mkdir -p -- "$out"
common=(-O2 -Wall -Wextra -Werror -Wno-cast-function-type -static)
x86_64-w64-mingw32-g++ "${common[@]}" -shared "$src/api_information_proxy.cpp" \
    -o "$out/api_information_proxy.dll" -lruntimeobject -lole32 -luuid
x86_64-w64-mingw32-gcc -shared -O2 -Wall -Wextra -Werror \
    "$src/sensor_monitor_unavailable.c" -o "$out/mfsensorgroup.dll"
x86_64-w64-mingw32-g++ "${common[@]}" "$src/runtime_bridge_test.cpp" \
    -o "$out/runtime_bridge_test.exe" -lruntimeobject -lole32 -luuid
