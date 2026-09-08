# syntax=docker/dockerfile:1.7
#
# SmartLogistics API image.
#
#   docker build -t smartlogistics-api .                  production image
#   docker build -t smartlogistics-api:dev --target dev . adds the test toolchain
#
# `runtime` is deliberately the last stage in this file, because the default
# build target is whatever comes last — putting `dev` there would quietly ship
# pytest and ruff to production.
#
# Stages:
#   uv           the uv binary, so the version is pinned rather than curl'd
#   base         interpreter, paths and the unprivileged user
#   builder      production dependencies, compiled into /opt/venv
#   dev-builder  the same venv plus the test toolchain
#   dev          venv from dev-builder, application source and tests
#   runtime      venv from builder and application source. No compiler, non-root.

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.9.5

# ----------------------------------------------------------------------- uv
# A named stage because an ARG declared before the first FROM expands only in
# FROM lines, not inside a COPY --from further down.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --------------------------------------------------------------------- base
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src

# A fixed uid/gid, so a mounted volume has predictable ownership on any host and
# the Kubernetes securityContext can name the same numbers.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app
# WORKDIR creates the directory as root; the process runs as `app` and needs to
# be able to write here (pytest's cache, for one).
RUN chown app:app /app

# ------------------------------------------------------------------ builder
FROM base AS builder

# No compiler here on purpose. Every compiled dependency — asyncpg,
# argon2-cffi-bindings, pydantic-core, uvloop, httptools — publishes manylinux
# wheels for both amd64 and arm64, so nothing is built from source. Installing
# build-essential "to be safe" would add several hundred megabytes and minutes
# to a stage that needs neither. If a dependency ever arrives without a wheel,
# pip says so plainly and the toolchain goes back into *this* stage only — the
# runtime image must never carry a compiler.
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN uv venv "$VIRTUAL_ENV"

# Dependencies are their own layer, rebuilt only when pyproject.toml changes —
# editing application code reinstalls nothing.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python "$VIRTUAL_ENV/bin/python" -r pyproject.toml

# -------------------------------------------------------------- dev-builder
FROM builder AS dev-builder

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python "$VIRTUAL_ENV/bin/python" \
        ruff pytest pytest-asyncio pytest-cov httpx

# ---------------------------------------------------------------------- dev
# Used by `docker compose --profile test` and by the compose override for local
# development with reload.
FROM base AS dev

COPY --from=dev-builder --chown=app:app /opt/venv /opt/venv
# pyproject.toml carries [tool.pytest.ini_options]. Without it pytest falls back
# to its defaults — asyncio_mode reverts to strict and every async test errors.
COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app src ./src
COPY --chown=app:app tests ./tests
COPY --chown=app:app deploy/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--reload", "--reload-dir", "/app/src"]

# ------------------------------------------------------------------ runtime
FROM base AS runtime

COPY --from=builder --chown=app:app /opt/venv /opt/venv

# alembic.ini resolves script_location relative to the working directory, so the
# migrations have to sit beside it for the migration Job to work.
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app src ./src
COPY --chown=app:app deploy/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
EXPOSE 8000

# curl and wget are absent from the slim image by design; the interpreter is
# already here, so this costs nothing extra.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# --proxy-headers so the client IP survives an ingress or load balancer — it is
# what `client_info` records against every session.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
