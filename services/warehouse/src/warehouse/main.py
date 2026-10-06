"""Warehouse HTTP process: ``uvicorn warehouse.main:app``."""

from sl_platform.app import create_service_app
from warehouse.api import warehouses
from warehouse.api.deps import verifier
from warehouse.config import settings
from warehouse.db import database
from warehouse.models import CONSTRAINT_MESSAGES

app = create_service_app(
    settings=settings,
    database=database,
    title="SmartLogistics Warehouse",
    description="Facilities, zones and operating hours. Operators are scoped to their own.",
    routers=[warehouses.router],
    tags_metadata=[{"name": "Warehouses", "description": "Facilities, zones, hours."}],
    constraint_messages=CONSTRAINT_MESSAGES,
    on_startup=[verifier.try_warm_up],
)
