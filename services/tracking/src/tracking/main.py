"""Tracking HTTP process: ``uvicorn tracking.main:app``."""

from sl_platform.app import create_service_app
from tracking.api import tracking
from tracking.api.deps import store, verifier
from tracking.config import settings

app = create_service_app(
    settings=settings,
    database=None,
    title="SmartLogistics Tracking",
    description=(
        "Shipment journeys and courier field updates, on MongoDB. Updates are "
        "deduplicated by the device's event id and placed on the timeline by when "
        "they happened, so retries and offline sync are safe."
    ),
    routers=[tracking.router],
    tags_metadata=[{"name": "Tracking", "description": "Journeys, field updates, public view."}],
    on_startup=[verifier.try_warm_up, store.ensure_indexes],
    on_shutdown=[store.close],
    readiness_checks={"mongo": store.ping},
)
