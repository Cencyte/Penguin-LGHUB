#!/usr/bin/env bash
set -euo pipefail
out=${1:?Provide a lab build directory}
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
mkdir -p -- "$out"
x86_64-w64-mingw32-g++ -O2 -Wall -Wextra -Werror -static-libgcc -static-libstdc++ \
    "$repo/tools/wine/dcomp_device_probe.cpp" -lole32 -luuid \
    -o "$out/dcomp_device_probe.exe"
