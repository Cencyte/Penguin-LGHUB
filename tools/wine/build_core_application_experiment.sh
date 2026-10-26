#!/usr/bin/env bash
# Deliberately separate from build_runtime_bridges.sh: no default deployment.
set -euo pipefail
src=$(cd -- "$(dirname -- "$0")" && pwd)
out=${1:?Provide an output directory}
mkdir -p -- "$out"
common=(-O2 -Wall -Wextra -Werror -Wno-cast-function-type -static)
x86_64-w64-mingw32-g++ "${common[@]}" -shared "$src/core_application_experiment.cpp" \
    -o "$out/core_application_experiment.dll" -lruntimeobject -lole32 -luuid
x86_64-w64-mingw32-g++ "${common[@]}" "$src/core_application_test.cpp" \
    -o "$out/core_application_test.exe" -lruntimeobject -lole32 -luuid
