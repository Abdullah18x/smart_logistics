"""Events Warehouse publishes on ``warehouse.events``.

Each carries the warehouse's full public snapshot (event-carried state
transfer), so Shipment and Inventory can keep local copies without ever
calling this service on their request paths.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sl_platform.events import Topics
from sl_platform.outbox import add_event
from warehouse.models import Warehouse

PRODUCER = "warehouse"


class WarehouseEvents:
    CREATED = "warehouse.created"
    UPDATED = "warehouse.updated"
    STATUS_CHANGED = "warehouse.status_changed"
    DELETED = "warehouse.deleted"
    RESTORED = "warehouse.restored"


def snapshot(warehouse: Warehouse) -> dict:
    return {
        "id": warehouse.id,
        "code": warehouse.code,
        "name": warehouse.name,
        "type": warehouse.type.value,
        "status": warehouse.status.value,
        "city": warehouse.city,
        "region": warehouse.region,
        "country_code": warehouse.country_code,
        "timezone": warehouse.timezone,
        "latitude": warehouse.latitude,
        "longitude": warehouse.longitude,
        "entrance_latitude": warehouse.entrance_latitude,
        "entrance_longitude": warehouse.entrance_longitude,
        "geofence_radius_m": warehouse.geofence_radius_m,
        "deleted": warehouse.deleted_at is not None,
        # Consumers ignore a snapshot older than the one they already hold.
        "updated_at": warehouse.updated_at,
    }


def emit(session: AsyncSession, event_type: str, warehouse: Warehouse, **extra) -> None:
    add_event(
        session,
        topic=Topics.WAREHOUSE,
        event_type=event_type,
        key=warehouse.id,
        payload={**snapshot(warehouse), **extra},
        producer=PRODUCER,
    )
