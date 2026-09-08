#!/usr/bin/env bash
#
# Container entrypoint.
#
# Waits for Postgres, optionally applies migrations and seed data, then hands
# control to whatever command the image was given.
#
#   WAIT_FOR_DB      wait for Postgres before starting   (default: true)
#   WAIT_TIMEOUT     seconds to keep trying              (default: 60)
#   RUN_MIGRATIONS   apply `alembic upgrade head` first  (default: false)
#   RUN_SEED         load development data first         (default: false)
#
# RUN_MIGRATIONS defaults to false on purpose. It is convenient for a single
# local container, but several replicas starting together would each try to
# migrate the same database at once. In Kubernetes the migration Job owns that
# step; see deploy/k8s/base/migration-job.yaml.

set -euo pipefail

log() { printf '%s  entrypoint  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

is_true() {
    case "${1,,}" in
        1 | true | yes | on) return 0 ;;
        *) return 1 ;;
    esac
}

if ! is_true "${WAIT_FOR_DB:-true}"; then
    log "database wait disabled"
elif [ -z "${DATABASE_URL:-}" ]; then
    # A one-off command — a shell, a version check — rather than the API.
    # Say so instead of dying on a missing environment variable.
    log "DATABASE_URL is not set, skipping the database wait"
else
    log "waiting for postgres (timeout ${WAIT_TIMEOUT:-60}s)"
    python - <<'PYTHON'
import asyncio
import os
import sys
import time

import asyncpg
from sqlalchemy.engine import make_url

url = make_url(os.environ["DATABASE_URL"])
deadline = time.monotonic() + float(os.environ.get("WAIT_TIMEOUT", "60"))
last_error = None


async def probe() -> None:
    connection = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host or "localhost",
        port=url.port or 5432,
        database=url.database,
        timeout=5,
    )
    await connection.close()


while time.monotonic() < deadline:
    try:
        asyncio.run(probe())
        print(f"postgres is accepting connections at {url.host}:{url.port or 5432}")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - any failure just means "not yet"
        last_error = exc
        time.sleep(1)

print(
    f"postgres did not become ready: {type(last_error).__name__}: {last_error}",
    file=sys.stderr,
)
sys.exit(1)
PYTHON
fi

if is_true "${RUN_MIGRATIONS:-false}"; then
    log "applying migrations"
    alembic upgrade head
fi

if is_true "${RUN_SEED:-false}"; then
    log "loading development data"
    python -m app.seeders.runner
fi

log "starting: $*"
exec "$@"
