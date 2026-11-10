"""The nine metrics, read from the daily rollups (and the courier replica for load).

Every query takes a date range (inclusive, UTC days) and the caller's warehouse
scope: ``None`` sees everything, a list restricts warehouse counters to those
warehouses and couriers to the ones based there.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.models import (
    Courier,
    DailyRollup,
    IssueKind,
    Metric,
    PipelineIssue,
    Scope,
    Shipment,
)
from analytics.rollups import start_of

Scoped = list[uuid.UUID] | None


@dataclass
class Counters:
    values: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def __getitem__(self, metric: Metric) -> int:
        return self.values.get(metric.value, 0)

    @property
    def avg_delivery_hours(self) -> float | None:
        timed = self[Metric.DELIVERED_TIMED]
        return round(self[Metric.DELIVERY_SECONDS] / timed / 3600, 2) if timed else None

    @property
    def return_rate(self) -> float | None:
        """Returns started per shipment dispatched in the period."""
        dispatched = self[Metric.DISPATCHED]
        return round(self[Metric.RETURNED] / dispatched, 4) if dispatched else None

    @property
    def failed_delivery_rate(self) -> float | None:
        attempts = self[Metric.DELIVERED] + self[Metric.DELIVERY_FAILED]
        return round(self[Metric.DELIVERY_FAILED] / attempts, 4) if attempts else None


@dataclass
class Utilisation:
    active_couriers: int
    busy_couriers: int
    active_assignments: int
    capacity: int

    @property
    def ratio(self) -> float | None:
        return round(self.active_assignments / self.capacity, 4) if self.capacity else None


def _rollups(scope: Scope, start: date, end: date) -> Select:
    return select(DailyRollup).where(
        DailyRollup.scope == scope, DailyRollup.day >= start, DailyRollup.day <= end
    )


class Reports:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def warehouse_counters(
        self, start: date, end: date, warehouses: Scoped
    ) -> dict[uuid.UUID, Counters]:
        query = _rollups(Scope.WAREHOUSE, start, end)
        if warehouses is not None:
            query = query.where(DailyRollup.scope_id.in_(warehouses))
        out: dict[uuid.UUID, Counters] = defaultdict(Counters)
        for row in await self.session.scalars(query):
            out[row.scope_id].values[row.metric] += row.value
        return out

    async def totals(self, start: date, end: date, warehouses: Scoped) -> Counters:
        total = Counters()
        for counters in (await self.warehouse_counters(start, end, warehouses)).values():
            for metric, value in counters.values.items():
                total.values[metric] += value
        return total

    async def utilisation(self, warehouses: Scoped) -> Utilisation:
        query = select(
            func.count(),
            func.count().filter(Courier.active_assignments > 0),
            func.coalesce(func.sum(Courier.active_assignments), 0),
            func.coalesce(func.sum(Courier.max_active_shipments), 0),
        ).where(Courier.status == "active", Courier.deleted.is_(False))
        if warehouses is not None:
            query = query.where(Courier.home_warehouse_id.in_(warehouses))
        active, busy, load, capacity = (await self.session.execute(query)).one()
        return Utilisation(active, busy, load, capacity)

    async def issue_counts(self, start: date, end: date) -> dict[str, int]:
        lower, upper = _bounds(start, end)
        rows = await self.session.execute(
            select(PipelineIssue.kind, func.count())
            .where(PipelineIssue.occurred_at >= lower, PipelineIssue.occurred_at < upper)
            .group_by(PipelineIssue.kind)
        )
        counts = {kind.value: 0 for kind in IssueKind}
        counts.update({kind.value: n for kind, n in rows.all()})
        return counts

    async def series(
        self, metric: Metric, start: date, end: date, warehouses: Scoped
    ) -> list[tuple[date, int]]:
        query = (
            select(DailyRollup.day, func.sum(DailyRollup.value))
            .where(
                DailyRollup.scope == Scope.WAREHOUSE,
                DailyRollup.metric == metric.value,
                DailyRollup.day >= start,
                DailyRollup.day <= end,
            )
            .group_by(DailyRollup.day)
        )
        if warehouses is not None:
            query = query.where(DailyRollup.scope_id.in_(warehouses))
        found = {day: int(value) for day, value in (await self.session.execute(query)).all()}
        days = (end - start).days + 1
        return [(d, found.get(d, 0)) for d in (start + timedelta(n) for n in range(days))]

    async def warehouse_codes(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = await self.session.execute(
            select(Shipment.warehouse_id, func.max(Shipment.warehouse_code))
            .where(Shipment.warehouse_id.in_(ids))
            .group_by(Shipment.warehouse_id)
        )
        return {wid: code for wid, code in rows.all() if code}

    async def courier_rows(
        self, start: date, end: date, warehouses: Scoped
    ) -> list[tuple[Courier | None, uuid.UUID, Counters]]:
        couriers = select(Courier).where(Courier.deleted.is_(False))
        if warehouses is not None:
            couriers = couriers.where(Courier.home_warehouse_id.in_(warehouses))
        known = {c.id: c for c in await self.session.scalars(couriers)}
        query = _rollups(Scope.COURIER, start, end)
        if warehouses is not None:
            query = query.where(DailyRollup.scope_id.in_(list(known)))
        counters: dict[uuid.UUID, Counters] = defaultdict(Counters)
        for row in await self.session.scalars(query):
            counters[row.scope_id].values[row.metric] += row.value
        ids = set(known) | set(counters)
        rows = [(known.get(cid), cid, counters.get(cid, Counters())) for cid in ids]
        return sorted(rows, key=lambda r: (-r[2][Metric.ASSIGNED], r[0].code if r[0] else ""))

    async def issues(
        self,
        start: date,
        end: date,
        *,
        kind: IssueKind | None,
        source: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PipelineIssue], int]:
        lower, upper = _bounds(start, end)
        where = [PipelineIssue.occurred_at >= lower, PipelineIssue.occurred_at < upper]
        if kind:
            where.append(PipelineIssue.kind == kind)
        if source:
            where.append(PipelineIssue.source == source)
        total = await self.session.scalar(
            select(func.count()).select_from(PipelineIssue).where(and_(*where))
        )
        rows = await self.session.scalars(
            select(PipelineIssue)
            .where(*where)
            .order_by(PipelineIssue.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return start_of(start), start_of(end + timedelta(days=1))
