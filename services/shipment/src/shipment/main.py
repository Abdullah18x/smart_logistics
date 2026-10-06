"""Shipment HTTP process: ``uvicorn shipment.main:app``."""

from shipment.api import shipments
from shipment.api.deps import inventory_client, verifier
from shipment.config import settings
from shipment.db import database
from shipment.models import CONSTRAINT_MESSAGES
from sl_platform.app import create_service_app

app = create_service_app(
    settings=settings,
    database=database,
    title="SmartLogistics Shipment",
    description=(
        "Shipment lifecycle, packing and state transitions. Reserves stock through the "
        "Inventory service; every change is published on `shipment.events`."
    ),
    routers=[shipments.router],
    tags_metadata=[{"name": "Shipments", "description": "Lifecycle, packing, audit trail."}],
    constraint_messages=CONSTRAINT_MESSAGES,
    on_startup=[verifier.try_warm_up],
    on_shutdown=[inventory_client.aclose],
)
