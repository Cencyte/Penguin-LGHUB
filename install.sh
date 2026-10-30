#!/bin/sh
# Run from a cloned checkout; never pipe downloaded code into a privileged shell.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$project_dir/tools/packaging/install.py" "$@"
