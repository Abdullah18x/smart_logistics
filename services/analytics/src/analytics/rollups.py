"""Which counters a milestone moves, applied incrementally or rebuilt from the facts.

One function, ``deltas``, decides what a milestone counts towards. The consumer
applies its result in the milestone's own transaction (so the dashboards move
within a second of the event); the nightly job replays every milestone of a
date range through the same function and replaces those days' rollups — so
whatever the incremental path got wrong (a bug since fixed, a manual data fix)
is corrected by the next morning.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.models import DailyRollup, Metric, Milestone, Scope, Shipment

#: Milestone kind -> the per-warehouse counter it bumps.
WAREHOUSE_METRIC = {
    "created": Metric.CREATED,
    "dispatched": Metric.DISPATCHED,
    "delivered": Metric.DELIVERED,
    "delivery_failed": Metric.DELIVERY_FAILED,
    "return_initiated": Metric.RETURNED,
    "cancelled": Metric.CANCELLED,
    "dispatch_failed": Metric.DISPATCH_FAILED,
}
#: Milestone kind -> the per-courier counter it bumps (when a courier is known).
COURIER_METRIC = {
    "assigned": Metric.ASSIGNED,
    "delivered": Metric.DELIVERED,
    "delivery_failed": Metric.DELIVERY_FAILED,
}
#: Every kind a milestone row is written for.
TRACKED = frozenset(WAREHOUSE_METRIC) | frozenset(COURIER_METRIC)


@dataclass(frozen=True)
class Delta:
    day: date
    scope: Scope
    scope_id: uuid.UUID
    metric: Metric
    value: int


def deltas(
    kind: str,
    occurred_at: datetime,
    warehouse_id: uuid.UUID | None,
    courier_id: uuid.UUID | None,
    created_at: datetime | None = None,
) -> list[Delta]:
    """The counters one milestone moves. ``created_at`` (when the shipment was
    created) lets a delivery add to the delivery-time total."""
    day = occurred_at.astimezone(UTC).date()
    out: list[Delta] = []
    metric = WAREHOUSE_METRIC.get(kind)
    if metric and warehouse_id:
        out.append(Delta(day, Scope.WAREHOUSE, warehouse_id, metric, 1))
        if kind == "delivered" and created_at and occurred_at >= created_at:
            seconds = int((occurred_at - created_at).total_seconds())
            out.append(Delta(day, Scope.WAREHOUSE, warehouse_id, Metric.DELIVERY_SECONDS, seconds))
            out.append(Delta(day, Scope.WAREHOUSE, warehouse_id, Metric.DELIVERED_TIMED, 1))
    metric = COURIER_METRIC.get(kind)
    if metric and courier_id:
        out.append(Delta(day, Scope.COURIER, courier_id, metric, 1))
    return out


async def apply(session: AsyncSession, changes: Iterable[Delta]) -> None:
    """Add to the counters (``value = value + delta``), creating missing rows."""
    merged: dict[tuple, int] = defaultdict(int)
    for d in changes:
        merged[(d.day, d.scope, d.scope_id, d.metric.value)] += d.value
    if not merged:
        return
    statement = pg_insert(DailyRollup).values(
        [
            {"day": day, "scope": scope, "scope_id": sid, "metric": metric, "value": value}
            for (day, scope, sid, metric), value in sorted(merged.items(), key=str)
        ]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["day", "scope", "scope_id", "metric"],
            set_={"value": DailyRollup.value + statement.excluded.value},
        )
    )


async def recompute(session: AsyncSession, start: date, end: date) -> int:
    """Rebuild every rollup from ``start`` to ``end`` (inclusive) from the
    milestones. Returns the number of milestones counted. The caller commits:
    readers see the old numbers or the new ones, never a half-built day."""
    lower, upper = start_of(start), start_of(end + timedelta(days=1))
    await session.execute(
        delete(DailyRollup).where(and_(DailyRollup.day >= start, DailyRollup.day <= end))
    )
    rows = await session.stream(
        select(Milestone, Shipment.created_at_source)
        .outerjoin(Shipment, Shipment.id == Milestone.shipment_id)
        .where(Milestone.occurred_at >= lower, Milestone.occurred_at < upper)
        .execution_options(yield_per=1000)
    )
    counted, batch = 0, []
    async for milestone, created_at in rows:
        batch.extend(
            deltas(
                milestone.kind,
                milestone.occurred_at,
                milestone.warehouse_id,
                milestone.courier_id,
                created_at,
            )
        )
        counted += 1
    await apply(session, batch)
    return counted


def start_of(day: date) -> datetime:
    """Midnight UTC at the start of ``day``."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)
