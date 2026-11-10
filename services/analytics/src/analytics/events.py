"""Analytics' consumer: shipment and courier events become facts and counters.

Every handler runs inside ``EventProcessor``'s transaction with its dedupe row,
so a milestone, the shipment state it updates and the counters it moves are
committed together, once.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics import rollups
from analytics.models import Courier, IssueKind, Milestone, PipelineIssue, Shipment
from sl_platform.consumer import EventProcessor
from sl_platform.events import EventEnvelope, Topics


def _uuid(value) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


async def record_milestone(
    session: AsyncSession,
    envelope: EventEnvelope,
    kind: str,
    *,
    shipment_id: uuid.UUID,
    warehouse_id: uuid.UUID | None,
    courier_id: uuid.UUID | None,
    reason: str | None = None,
) -> bool:
    """Store the fact and move its counters. False if this event was already counted."""
    inserted = await session.scalar(
        pg_insert(Milestone)
        .values(
            event_id=envelope.event_id,
            shipment_id=shipment_id,
            kind=kind,
            occurred_at=envelope.occurred_at,
            warehouse_id=warehouse_id,
            courier_id=courier_id,
            reason=reason,
        )
        .on_conflict_do_nothing()
        .returning(Milestone.event_id)
    )
    if inserted is None:
        return False
    created_at = None
    if kind == "delivered":
        created_at = await session.scalar(
            select(Shipment.created_at_source).where(Shipment.id == shipment_id)
        )
    await rollups.apply(
        session, rollups.deltas(kind, envelope.occurred_at, warehouse_id, courier_id, created_at)
    )
    return True


async def upsert_shipment(session: AsyncSession, payload: dict, occurred_at: datetime) -> None:
    """Keep the latest state; an event older than what is stored (by the shipment's
    own version) does not roll the status back, but its timestamps still count."""
    status = payload["status"]
    values = {
        "id": _uuid(payload["id"]),
        "reference_no": payload["reference_no"],
        "status": status,
        "version": payload.get("version") or 0,
        "service_level": payload.get("service_level"),
        "warehouse_id": _uuid(payload.get("origin_warehouse_id")),
        "warehouse_code": payload.get("origin_warehouse_code"),
        "courier_id": _uuid(payload.get("courier_id")),
    }
    stamps = {}
    if status == "created":
        stamps["created_at_source"] = occurred_at
    elif status == "dispatched":
        stamps["dispatched_at"] = occurred_at
    elif status == "delivered":
        stamps["delivered_at"] = occurred_at
    statement = pg_insert(Shipment).values(
        **values, **stamps, failed_attempts=1 if status == "delivery_failed" else 0
    )
    newer = statement.excluded.version > Shipment.version

    def latest(column: str):
        return case((newer, getattr(statement.excluded, column)), else_=getattr(Shipment, column))

    set_ = {
        column: latest(column)
        for column in ("status", "version", "service_level", "courier_id", "warehouse_code")
    }
    set_["reference_no"] = statement.excluded.reference_no
    set_["warehouse_id"] = statement.excluded.warehouse_id
    for column in stamps:
        set_[column] = getattr(statement.excluded, column)
    if status == "delivery_failed":
        set_["failed_attempts"] = Shipment.failed_attempts + 1
    await session.execute(statement.on_conflict_do_update(index_elements=["id"], set_=set_))


async def on_shipment(session: AsyncSession, envelope: EventEnvelope) -> None:
    payload = envelope.payload
    shipment_id = _uuid(payload["id"])
    status = payload["status"] if envelope.event_type != "shipment.created" else "created"
    await upsert_shipment(session, {**payload, "status": status}, envelope.occurred_at)
    if status not in rollups.TRACKED:
        return
    counted = await record_milestone(
        session,
        envelope,
        status,
        shipment_id=shipment_id,
        warehouse_id=_uuid(payload.get("origin_warehouse_id")),
        courier_id=_uuid(payload.get("courier_id")),
        reason=payload.get("reason"),
    )
    if counted and status == "dispatch_failed":
        reason = payload.get("reason") or "unknown"
        await session.execute(
            pg_insert(PipelineIssue)
            .values(
                id=uuid.uuid4(),
                kind=IssueKind.DISPATCH_FAILED,
                source=payload.get("origin_warehouse_code") or "unknown",
                occurred_at=envelope.occurred_at,
                reason=reason.split(":", 1)[0].strip()[:80],
                detail=reason,
                event_id=str(envelope.event_id),
                event_type=envelope.event_type,
                shipment_id=shipment_id,
                origin=f"event:{envelope.event_id}",
            )
            .on_conflict_do_nothing()
        )


async def on_assigned(session: AsyncSession, envelope: EventEnvelope) -> None:
    payload = envelope.payload
    shipment_id, courier_id = _uuid(payload["shipment_id"]), _uuid(payload["courier_id"])
    await record_milestone(
        session,
        envelope,
        "assigned",
        shipment_id=shipment_id,
        warehouse_id=_uuid(payload.get("warehouse_id")),
        courier_id=courier_id,
    )
    await session.execute(
        update(Shipment).where(Shipment.id == shipment_id).values(courier_id=courier_id)
    )


async def on_courier(session: AsyncSession, envelope: EventEnvelope) -> None:
    payload = envelope.payload
    stamp = payload.get("updated_at")
    updated_at = datetime.fromisoformat(stamp) if stamp else envelope.occurred_at
    values = {
        "id": _uuid(payload["id"]),
        "code": payload["code"],
        "full_name": payload["full_name"],
        "status": payload["status"],
        "home_warehouse_id": _uuid(payload.get("home_warehouse_id")),
        "max_active_shipments": payload["max_active_shipments"],
        "active_assignments": payload["active_assignments"],
        "deleted": bool(payload.get("deleted")) or envelope.event_type == "courier.deleted",
        "updated_at": updated_at,
    }
    statement = pg_insert(Courier).values(**values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["id"],
            set_={k: getattr(statement.excluded, k) for k in values if k != "id"},
            where=Courier.updated_at <= statement.excluded.updated_at,
        )
    )


COURIER_PROFILE_EVENTS = (
    "courier.created",
    "courier.updated",
    "courier.status_changed",
    "courier.availability_changed",
    "courier.deleted",
    "courier.restored",
)


def build_processor(sessionmaker: async_sessionmaker[AsyncSession]) -> EventProcessor:
    handlers = {
        "shipment.created": on_shipment,
        "shipment.status_changed": on_shipment,
        "courier.assigned": on_assigned,
        **dict.fromkeys(COURIER_PROFILE_EVENTS, on_courier),
    }
    return EventProcessor("analytics", sessionmaker, handlers, (Topics.SHIPMENT, Topics.COURIER))
