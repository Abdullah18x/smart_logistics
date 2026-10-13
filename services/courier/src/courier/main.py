"""Courier HTTP process: ``uvicorn courier.main:app``."""

from courier.api import assignments, couriers
from courier.api.deps import verifier
from courier.config import settings
from courier.db import database
from courier.models import CONSTRAINT_MESSAGES
from sl_platform.app import create_service_app

app = create_service_app(
    settings=settings,
    database=database,
    title="SmartLogistics Courier",
    description=(
        "Fleet profiles, availability and shipment assignments. Assignment is automatic "
        "(same city, available, least loaded) with a manual override."
    ),
    # Assignments first: its fixed paths must win over /{courier_id}.
    routers=[assignments.router, couriers.router],
    tags_metadata=[
        {"name": "Couriers", "description": "Profiles and availability."},
        {"name": "Assignments", "description": "Who carries which shipment."},
    ],
    constraint_messages=CONSTRAINT_MESSAGES,
    on_startup=[verifier.try_warm_up],
)
