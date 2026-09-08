"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.controllers import api_router, health_router
from app.core.config import settings
from app.core.database import engine
from app.core.db_errors import translate
from app.core.exceptions import DomainError

DESCRIPTION = """
Backend for **SmartLogistics** — TransFleet's supply chain and delivery
orchestration platform.

Authenticate at `POST /api/v1/auth/login`, then use **Authorize** to send the
access token with subsequent requests.
"""

TAGS_METADATA = [
    {"name": "Authentication", "description": "Login, token refresh, logout and sessions."},
    {"name": "Users", "description": "Account management and role administration."},
    {
        "name": "Warehouses",
        "description": "Facilities, zones and operating hours. Operators are scoped to their own.",
    },
    {
        "name": "Inventory",
        "description": "Stock levels and holds. Reservations are driven by the shipment lifecycle.",
    },
    {
        "name": "Shipments",
        "description": (
            "Shipment lifecycle, packing and state transitions. Results are row-scoped: "
            "operators see their warehouses, couriers see their own assignments."
        ),
    },
    {"name": "Health", "description": "Liveness and readiness probes."},
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        """One translation point from domain failures to HTTP responses."""
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
            headers=headers,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
        """Database constraints are the authority on integrity.

        Rather than duplicating referential and uniqueness checks in Python —
        where a check-then-act is a race anyway — the constraint is allowed to
        fire and is translated into a meaningful response here.
        """
        domain_error = translate(exc)
        return JSONResponse(
            status_code=domain_error.status_code,
            content={"code": domain_error.code, "message": domain_error.message},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Request validation failed.",
                "detail": [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "error": e["msg"]}
                    for e in exc.errors()
                ],
            },
        )

    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()
