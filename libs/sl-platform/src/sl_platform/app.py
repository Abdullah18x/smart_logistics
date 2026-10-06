"""Application factory shared by every service's HTTP process."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response, status
from sqlalchemy import text

from sl_platform.config import ServiceSettings
from sl_platform.db import Database
from sl_platform.errors import install_error_handlers
from sl_platform.logging_setup import configure_logging, request_id_var
from sl_platform.telemetry import setup_telemetry

Hook = Callable[[], Awaitable[Any]]


def health_router(settings: ServiceSettings, database: Database) -> APIRouter:
    router = APIRouter(tags=["Health"])

    @router.get("/health", summary="Liveness probe")
    async def health() -> dict:
        """Is the process up? Deliberately touches nothing else, so a database
        hiccup never makes Kubernetes restart every pod."""
        return {"status": "ok", "service": settings.service_name}

    @router.get("/health/ready", summary="Readiness probe — checks this service's database")
    async def ready(response: Response) -> dict:
        try:
            async with database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            checks = {"postgres": "ok"}
        except Exception as exc:
            checks = {"postgres": f"error: {exc.__class__.__name__}"}
        ok = all(v == "ok" for v in checks.values())
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ok else "degraded", "checks": checks}

    return router


def create_service_app(
    *,
    settings: ServiceSettings,
    database: Database,
    title: str,
    description: str,
    routers: Sequence[APIRouter],
    tags_metadata: list[dict] | None = None,
    constraint_messages: dict[str, str] | None = None,
    on_startup: Sequence[Hook] = (),
    on_shutdown: Sequence[Hook] = (),
) -> FastAPI:
    configure_logging(settings.service_name, settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for hook in on_startup:
            await hook()
        yield
        for hook in on_shutdown:
            await hook()
        await database.dispose()

    app = FastAPI(
        title=title,
        description=description,
        version="0.2.0",
        openapi_tags=[*(tags_metadata or []), {"name": "Health", "description": "Probes."}],
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        value = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(value)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = value
        return response

    install_error_handlers(app, constraint_messages)
    app.include_router(health_router(settings, database))
    for router in routers:
        app.include_router(router)
    setup_telemetry(
        app,
        service_name=settings.service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
        engine=database.engine,
    )
    return app
