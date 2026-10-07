#!/usr/bin/env sh
# SmartLogistics developer CLI for macOS and Linux.
#   ./dev.sh help      ./dev.sh infra up      ./dev.sh start
# All logic lives in scripts/dev.py, shared with dev.ps1 (Windows).
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi
# First run: build the workspace virtualenv the CLI runs in.
if [ ! -x .venv/bin/python ]; then
    uv sync --all-packages
fi
if [ $# -eq 0 ] || [ "$1" = "help" ]; then
    set -- --help
fi
exec uv run --no-sync python scripts/dev.py "$@"
