"""Events Courier publishes (``courier.events``) and consumes.

Publishes profile snapshots (Identity mirrors the user link from them) and
assignment changes (Shipment mirrors ``courier_id`` from them). Consumes
warehouse snapshots for its replica and shipment status changes, which end
assignments: a delivered or returned shipment completes it, a cancelled one or
a failed dispatch releases it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from courier.models import Courier, CourierAssignment
from sl_platform.consumer import EventProcessor
from sl_platform.events import EventEnvelope, Topics
from sl_platform.outbox import add_event

PRODUCER = "courier"


class CourierEvents:
    CREATED = "courier.created"
    UPDATED = "courier.updated"
    STATUS_CHANGED = "courier.status_changed"
    AVAILABILITY_CHANGED = "courier.availability_changed"
    DELETED = "courier.deleted"
    RESTORED = "courier.restored"
    ASSIGNED = "courier.assigned"
    UNASSIGNED = "courier.unassigned"


def snapshot(courier: Courier) -> dict:
    return {
        "id": courier.id,
        "code": courier.code,
        "user_id": courier.user_id,
        "full_name": courier.full_name,
        "vehicle_type": courier.vehicle_type.value,
        "home_city": courier.home_city,
        "home_warehouse_id": courier.home_warehouse_id,
        "status": courier.status.value,
        "availability": courier.availability.value,
        "max_active_shipments": courier.max_active_shipments,
        "active_assignments": courier.active_assignments,
        "deleted": courier.deleted_at is not None,
        "updated_at": courier.updated_at,
    }


def emit(session: AsyncSession, event_type: str, courier: Courier, **extra) -> None:
    add_event(
        session,
        topic=Topics.COURIER,
        event_type=event_type,
        key=courier.id,
        payload={**snapshot(courier), **extra},
        producer=PRODUCER,
    )


def emit_assignment(session: AsyncSession, event_type: str, assignment: CourierAssignment) -> None:
    # Keyed by shipment, so one shipment's hand-overs stay in order.
    add_event(
        session,
        topic=Topics.COURIER,
        event_type=event_type,
        key=assignment.shipment_id,
        payload={
            "assignment_id": assignment.id,
            "shipment_id": assignment.shipment_id,
            "courier_id": assignment.courier_id,
            "warehouse_id": assignment.warehouse_id,
            "status": assignment.status.value,
            "method": assignment.method.value,
            "assigned_at": assignment.assigned_at,
            "ended_at": assignment.ended_at,
            "end_reason": assignment.end_reason,
        },
        producer=PRODUCER,
    )


#: Shipment statuses that end the courier's job, and how.
COMPLETING_STATUSES = frozenset({"delivered", "returned"})
RELEASING_STATUSES = frozenset({"cancelled", "dispatch_failed"})


async def on_warehouse_snapshot(session: AsyncSession, event: EventEnvelope) -> None:
    from courier.services.replicas import upsert_warehouse

    await upsert_warehouse(session, event.payload)


async def on_shipment_status_changed(session: AsyncSession, event: EventEnvelope) -> None:
    from courier.services.assignments import AssignmentService

    status = event.payload.get("status")
    shipment_id = uuid.UUID(str(event.payload["id"]))
    service = AssignmentService(session)
    if status in COMPLETING_STATUSES:
        await service.end(shipment_id, completed=True, reason=f"shipment {status}")
    elif status in RELEASING_STATUSES:
        await service.end(shipment_id, completed=False, reason=f"shipment {status}")


def build_processor(sessionmaker) -> EventProcessor:
    handlers = {
        name: on_warehouse_snapshot
        for name in (
            "warehouse.created",
            "warehouse.updated",
            "warehouse.status_changed",
            "warehouse.deleted",
            "warehouse.restored",
        )
    }
    handlers["shipment.status_changed"] = on_shipment_status_changed
    return EventProcessor(
        "courier", sessionmaker, handlers=handlers, topics=(Topics.WAREHOUSE, Topics.SHIPMENT)
    )
