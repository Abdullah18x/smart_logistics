"""Events Inventory publishes (``inventory.events``) and the ones it consumes."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from sl_platform.consumer import EventProcessor
from sl_platform.events import EventEnvelope, Topics
from sl_platform.outbox import add_event

if TYPE_CHECKING:
    from inventory.models import InventoryReservation

logger = logging.getLogger("inventory.events")
PRODUCER = "inventory"


class StockEvents:
    RESERVED = "stock.reserved"
    COMMITTED = "stock.committed"
    RELEASED = "stock.released"
    ADJUSTED = "stock.adjusted"


def emit_stock_event(
    session: AsyncSession,
    event_type: str,
    shipment_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    holds: list[InventoryReservation],
    **extra,
) -> None:
    add_event(
        session,
        topic=Topics.INVENTORY,
        event_type=event_type,
        key=shipment_id,
        payload={
            "shipment_id": shipment_id,
            "warehouse_id": warehouse_id,
            "lines": [{"sku_id": h.sku_id, "quantity": h.quantity} for h in holds],
            **extra,
        },
        producer=PRODUCER,
    )


# --- consumed ----------------------------------------------------------------------


async def on_warehouse_snapshot(session: AsyncSession, event: EventEnvelope) -> None:
    from inventory.services.replicas import upsert_warehouse

    await upsert_warehouse(session, event.payload)


def _reservations(session: AsyncSession):
    from inventory.config import settings
    from inventory.services.reservations import ReservationService

    return ReservationService(
        session, unconfirmed_hold=timedelta(minutes=settings.unconfirmed_hold_minutes)
    )


async def on_shipment_created(session: AsyncSession, event: EventEnvelope) -> None:
    """The shipment was recorded: confirm its hold so it never expires."""
    confirmed = await _reservations(session).confirm(uuid.UUID(event.payload["id"]))
    if not confirmed:
        logger.warning("shipment %s created but holds no stock to confirm", event.payload["id"])


async def on_shipment_status_changed(session: AsyncSession, event: EventEnvelope) -> None:
    """Stock follows the lifecycle: dispatched commits, cancelled releases."""
    shipment_id = uuid.UUID(event.payload["id"])
    status = event.payload["status"]
    service = _reservations(session)
    if status == "dispatched":
        await service.commit(shipment_id)
    elif status == "cancelled":
        await service.release(shipment_id, event.payload.get("reason") or "Shipment cancelled")


def build_processor(sessionmaker) -> EventProcessor:
    return EventProcessor(
        "inventory",
        sessionmaker,
        handlers={
            "warehouse.created": on_warehouse_snapshot,
            "warehouse.updated": on_warehouse_snapshot,
            "warehouse.status_changed": on_warehouse_snapshot,
            "warehouse.deleted": on_warehouse_snapshot,
            "warehouse.restored": on_warehouse_snapshot,
            "shipment.created": on_shipment_created,
            "shipment.status_changed": on_shipment_status_changed,
        },
        topics=(Topics.WAREHOUSE, Topics.SHIPMENT),
    )
