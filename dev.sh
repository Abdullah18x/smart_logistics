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
# Keep the workspace virtualenv in step with uv.lock before every command: it
# installs every service on the first run, repairs a stale .venv (for example
# one left by the old monolith), and is a no-op taking milliseconds otherwise.
uv sync --all-packages --quiet
if [ $# -eq 0 ] || [ "$1" = "help" ]; then
    set -- --help
fi
exec uv run --no-sync python scripts/dev.py "$@"
