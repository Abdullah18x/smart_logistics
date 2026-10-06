"""Inventory HTTP process: ``uvicorn inventory.main:app``."""

from inventory.api import reservations, skus, stock
from inventory.api.deps import verifier
from inventory.config import settings
from inventory.db import database
from inventory.models import CONSTRAINT_MESSAGES
from sl_platform.app import create_service_app

app = create_service_app(
    settings=settings,
    database=database,
    title="SmartLogistics Inventory",
    description="SKU catalog, stock levels, stock holds and the stock ledger.",
    # Reservations first: its fixed paths must win over /inventory/{item_id}.
    routers=[reservations.router, stock.router, skus.router],
    tags_metadata=[
        {"name": "Reservations", "description": "Stock holds, called by Shipment."},
        {"name": "Inventory", "description": "Stock levels, adjustments, ledger."},
        {"name": "Catalog", "description": "SKUs."},
    ],
    constraint_messages=CONSTRAINT_MESSAGES,
    on_startup=[verifier.try_warm_up],
)
