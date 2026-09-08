from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.controllers.dependencies import SessionDep
from app.core.config import settings

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    """Is the process up? Deliberately touches nothing else.

    A liveness probe that checked the database would have Kubernetes restart
    every pod whenever Postgres hiccuped, which fixes nothing and turns a
    dependency outage into an outage of everything.
    """
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@router.get(
    "/health/ready",
    summary="Readiness probe — checks dependencies",
    responses={503: {"description": "A dependency is unreachable"}},
)
async def readiness(response: Response, session: SessionDep) -> dict:
    """Can this instance serve traffic?

    Returns 503 when it cannot. A readiness probe is read by its status code,
    so answering 200 with ``{"status": "degraded"}`` would leave a pod that
    cannot reach its database sitting in the load balancer taking requests.
    """
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {exc.__class__.__name__}"

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "degraded", "checks": checks}
