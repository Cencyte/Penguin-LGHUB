#!/usr/bin/env bash
# Usage: bash run_private_kernelbase.sh PRIVATE_DLL COPIED_PREFIX COMMAND [ARGS...]
# Run from an isolated display (e.g. Xvfb). Does not start/stop any X server.
# The system DLL is shadowed only in this child's private mount namespace.
set -euo pipefail
if (( $# < 3 )); then
    echo 'Usage: run_private_kernelbase.sh PRIVATE_DLL COPIED_PREFIX COMMAND [ARGS...]' >&2
    exit 2
fi
dll=$(realpath -e -- "$1")
prefix=$(realpath -e -- "$2")
shift 2
system_dll=/usr/lib/wine/x86_64-windows/kernelbase.dll
if [[ "$dll" == "$system_dll" || "$prefix" == "$HOME/.wine" || ! -d "$prefix/drive_c" ]]; then
    echo 'Provide a separate probe DLL and a copied Wine prefix.' >&2
    exit 2
fi
expected=8f8c1a49c143227b4b6bbb792b5d058de97e88577cf878dd3020c3a1b221bcac
actual=$(sha256sum -- "$dll")
if [[ ${actual%% *} != "$expected" ]]; then
    echo 'DLL does not match the reviewed Wine 10.20 probe.' >&2
    exit 2
fi
# Only the mount operation runs as root. Command and Wine use the caller's uid.
exec sudo unshare --mount --propagation private bash -c '
    set -euo pipefail
    dll=$1; target=$2; user=$3; prefix=$4; display=$5; authority=$6; debug=$7
    shift 7
    mount --bind "$dll" "$target"
    exec runuser -u "$user" -- env WINEPREFIX="$prefix" DISPLAY="$display" \
        XAUTHORITY="$authority" WINEDEBUG="$debug" \
        WINEDLLOVERRIDES=winemenubuilder.exe=d "$@"
' _ "$dll" "$system_dll" "$(id -un)" "$prefix" "${DISPLAY:?Set an isolated DISPLAY}" \
    "${XAUTHORITY:-$HOME/.Xauthority}" "${WINEDEBUG:--all}" "$@"
