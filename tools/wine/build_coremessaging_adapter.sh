#!/usr/bin/env bash
# Lab experiment, separate from the default bridge deployment.
set -euo pipefail
src=$(cd -- "$(dirname -- "$0")" && pwd)
out=${1:?Provide an output directory}
mode=${2:-}
if [[ -n $mode && $mode != --dispatcher-queue ]]; then
    echo "Unknown option: $mode" >&2
    exit 2
fi
mkdir -p -- "$out"
exports="$src/coremessaging_xp_adapter.def"
if [[ $mode == --dispatcher-queue ]]; then
    exports="$out/coremessaging_dispatcher.def"
    sed -e 's/CreateDispatcherQueueController=ghub_wine_coremessaging\./CreateDispatcherQueueController=ghub_dispatcher_queue./' \
        -e 's/DllGetActivationFactory=ghub_wine_coremessaging\./DllGetActivationFactory=ghub_dispatcher_queue./' \
        "$src/coremessaging_xp_adapter.def" > "$exports"
    x86_64-w64-mingw32-g++ -shared -std=c++17 -O2 -Wall -Wextra -Werror \
        -Wno-cast-function-type -static \
        "$src/dispatcher_queue_experiment.cpp" "$src/tray_popup_anchor.cpp" "$src/dispatcher_queue_experiment.def" \
        -lruntimeobject -lole32 -luuid -luser32 -lcomctl32 \
        -o "$out/ghub_dispatcher_queue.dll"
    x86_64-w64-mingw32-gcc -municode -O2 -Wall -Wextra -Werror \
        "$src/dispatcher_queue_test.c" -lruntimeobject -lole32 -luuid -luser32 \
        -o "$out/dispatcher_queue_test.exe"
fi
x86_64-w64-mingw32-gcc -shared -O2 -Wall -Wextra -Werror \
    "$src/coremessaging_xp_adapter.c" "$exports" \
    -o "$out/coremessaging.dll"
x86_64-w64-mingw32-gcc -municode -O2 -Wall -Wextra -Werror \
    "$src/coremessaging_adapter_test.c" -lole32 -ladvapi32 -luser32 \
    -o "$out/coremessaging_adapter_test.exe"
