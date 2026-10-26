#!/usr/bin/env bash
# Only rebuild Shcore in an existing configured Wine 10.20 build tree.
set -euo pipefail
src=$(realpath -e -- "${1:?Provide a Wine 10.20 source directory}")
out=$(realpath -e -- "${2:?Provide its configured build directory}")
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
[[ $(cat "$src/VERSION") == 'Wine version 10.20' && -f "$out/Makefile" ]]
python3 "$repo/tools/wine/apply_patch_series.py" "$src" "$repo/patches/wine/0032-shcore-record-feature-usage.patch"
make -C "$out" -j4 dlls/shcore/x86_64-windows/shcore.dll
cat > "$out/feature_usage_api.def" <<'EOF'
LIBRARY api-ms-win-core-featurestaging-l1-1-0.dll
EXPORTS
RecordFeatureUsage
EOF
x86_64-w64-mingw32-dlltool -d "$out/feature_usage_api.def" --output-delaylib "$out/libfeature_usage_delay.a"
x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -Werror "$repo/tools/wine/feature_usage_test.c" \
    "$out/libfeature_usage_delay.a" -ldelayimp -o "$out/feature_usage_test.exe"
