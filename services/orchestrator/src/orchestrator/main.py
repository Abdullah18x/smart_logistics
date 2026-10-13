"""Orchestrator HTTP process: ``uvicorn orchestrator.main:app``.

The workflows run in the worker process; this API only reports on them.
"""

from orchestrator.api import dispatches
from orchestrator.config import settings
from sl_platform.app import create_service_app

app = create_service_app(
    settings=settings,
    database=None,
    title="SmartLogistics Orchestrator",
    description=(
        "Dispatch and delivery workflows on Temporal. Dispatches start from "
        "`POST /api/v1/shipments/{id}/dispatch`; this API shows their progress."
    ),
    routers=[dispatches.router],
    tags_metadata=[{"name": "Dispatches", "description": "Dispatch workflow progress."}],
    on_startup=[dispatches.verifier.try_warm_up],
    readiness_checks={"temporal": dispatches.temporal.check},
)
