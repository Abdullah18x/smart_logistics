# syntax=docker/dockerfile:1.7
#
# One Dockerfile for every service; SERVICE picks which one.
#
#   docker build -f infra/docker/service.Dockerfile --build-arg SERVICE=identity -t sl-identity .
#
# The same image runs the HTTP process (default CMD) and the worker
# (`python -m <service>.worker`), so they scale independently.

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.9.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
ARG SERVICE
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# The lockfile pins every dependency, so every build of a commit is identical.
COPY pyproject.toml uv.lock ./
COPY libs ./libs
COPY services ./services
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package "${SERVICE}-service"

FROM python:${PYTHON_VERSION}-slim AS runtime
ARG SERVICE
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    SERVICE=${SERVICE}
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder --chown=app:app /opt/venv /opt/venv
COPY --chown=app:app services/${SERVICE}/alembic.ini ./alembic.ini
COPY --chown=app:app services/${SERVICE}/migrations ./migrations
COPY --chown=app:app infra/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn ${SERVICE}.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
