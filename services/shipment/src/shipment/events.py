"""Events Shipment publishes (``shipment.events``) and consumes."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shipment.models import Shipment
from sl_platform.consumer import EventProcessor
from sl_platform.events import EventEnvelope, Topics
from sl_platform.outbox import add_event

PRODUCER = "shipment"


class ShipmentEvents:
    CREATED = "shipment.created"
    UPDATED = "shipment.updated"
    STATUS_CHANGED = "shipment.status_changed"


def summary(shipment: Shipment) -> dict:
    return {
        "id": shipment.id,
        "reference_no": shipment.reference_no,
        "customer_reference": shipment.customer_reference,
        "status": shipment.status.value,
        "service_level": shipment.service_level.value,
        "priority": shipment.priority.value,
        "origin_warehouse_id": shipment.origin_warehouse_id,
        "origin_warehouse_code": shipment.origin_warehouse_code,
        "courier_id": shipment.courier_id,
        "total_weight_g": shipment.total_weight_g,
        "promised_delivery_at": shipment.promised_delivery_at,
        "version": shipment.version,
    }


def emit_created(session: AsyncSession, shipment: Shipment) -> None:
    add_event(
        session,
        topic=Topics.SHIPMENT,
        event_type=ShipmentEvents.CREATED,
        key=shipment.id,
        payload={
            **summary(shipment),
            "created_by": shipment.created_by,
            "destination_city": shipment.destination_address.city,
            "items": [{"sku_id": i.sku_id, "quantity": i.quantity} for i in shipment.items],
        },
        producer=PRODUCER,
    )


def emit(session: AsyncSession, event_type: str, shipment: Shipment, **extra) -> None:
    add_event(
        session,
        topic=Topics.SHIPMENT,
        event_type=event_type,
        key=shipment.id,
        payload={**summary(shipment), **extra},
        producer=PRODUCER,
    )


async def on_warehouse_snapshot(session: AsyncSession, event: EventEnvelope) -> None:
    from shipment.services.replicas import upsert_warehouse

    await upsert_warehouse(session, event.payload)


def build_processor(sessionmaker) -> EventProcessor:
    return EventProcessor(
        "shipment",
        sessionmaker,
        handlers={
            name: on_warehouse_snapshot
            for name in (
                "warehouse.created",
                "warehouse.updated",
                "warehouse.status_changed",
                "warehouse.deleted",
                "warehouse.restored",
            )
        },
        topics=(Topics.WAREHOUSE,),
    )
