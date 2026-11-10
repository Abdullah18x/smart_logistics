"""Analytics HTTP process: ``uvicorn analytics.main:app``."""

from analytics.api import analytics
from analytics.api.deps import verifier
from analytics.config import settings
from analytics.db import database
from analytics.models import CONSTRAINT_MESSAGES
from sl_platform.app import create_service_app

app = create_service_app(
    settings=settings,
    database=database,
    title="SmartLogistics Analytics",
    description=(
        "Operational metrics built from the event stream: shipments created, dispatched "
        "and delivered, failed deliveries, delivery time, courier utilisation, warehouse "
        "throughput, return rate and failed events. Counters move as events arrive and "
        "are rebuilt nightly from the facts."
    ),
    routers=[analytics.router],
    tags_metadata=[{"name": "Analytics", "description": "Read-only metrics."}],
    constraint_messages=CONSTRAINT_MESSAGES,
    on_startup=[verifier.try_warm_up],
)
