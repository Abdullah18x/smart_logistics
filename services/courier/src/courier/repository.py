"""Database access for couriers and assignments. Flush only; endpoints commit."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from courier.models import (
    AssignmentStatus,
    Availability,
    Courier,
    CourierAssignment,
    CourierStatus,
)


class CourierRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, courier_id: uuid.UUID, *, include_deleted: bool = False) -> Courier | None:
        query = select(Courier).where(Courier.id == courier_id)
        if not include_deleted:
            query = query.where(Courier.deleted_at.is_(None))
        return await self.session.scalar(query.execution_options(populate_existing=True))

    async def lock(self, courier_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Courier]:
        """Lock several couriers in id order, so two transactions never deadlock."""
        rows = await self.session.scalars(
            select(Courier)
            .where(Courier.id.in_(courier_ids))
            .order_by(Courier.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {c.id: c for c in rows}

    async def pick_assignable(
        self,
        *,
        city: str,
        warehouse_id: uuid.UUID,
        weight_g: int | None,
        exclude: Sequence[uuid.UUID] = (),
    ) -> Courier | None:
        """The best available courier for a warehouse, locked for this transaction.

        Same city, active, available, below capacity and able to carry the
        weight. Couriers based at this warehouse first, then the least loaded
        relative to capacity, then whoever has waited longest. ``SKIP LOCKED``:
        two dispatches running at once take different couriers instead of
        queueing on (and over-booking) the same one.
        """
        query = (
            select(Courier)
            .where(
                Courier.deleted_at.is_(None),
                Courier.status == CourierStatus.ACTIVE,
                Courier.availability == Availability.AVAILABLE,
                func.lower(Courier.home_city) == city.lower(),
                Courier.active_assignments < Courier.max_active_shipments,
            )
            .order_by(
                case((Courier.home_warehouse_id == warehouse_id, 0), else_=1),
                (Courier.active_assignments * 1.0 / Courier.max_active_shipments),
                Courier.last_assigned_at.asc().nulls_first(),
                Courier.code,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if weight_g is not None:
            query = query.where(
                or_(Courier.max_weight_g.is_(None), Courier.max_weight_g >= weight_g)
            )
        if exclude:
            query = query.where(Courier.id.not_in(exclude))
        return await self.session.scalar(query.execution_options(populate_existing=True))

    async def list(
        self,
        *,
        city: str | None,
        status: CourierStatus | None,
        availability: Availability | None,
        warehouse_id: uuid.UUID | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Courier], int]:
        filters = [Courier.deleted_at.is_(None)]
        if city:
            filters.append(func.lower(Courier.home_city) == city.lower())
        if status:
            filters.append(Courier.status == status)
        if availability:
            filters.append(Courier.availability == availability)
        if warehouse_id:
            filters.append(Courier.home_warehouse_id == warehouse_id)
        if search:
            like = f"%{search.lower()}%"
            filters.append(
                or_(func.lower(Courier.full_name).like(like), func.lower(Courier.code).like(like))
            )
        total = await self.session.scalar(select(func.count()).select_from(Courier).where(*filters))
        rows = await self.session.scalars(
            select(Courier).where(*filters).order_by(Courier.code).limit(limit).offset(offset)
        )
        return list(rows), total or 0


class AssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active_for_shipment(
        self, shipment_id: uuid.UUID, *, lock: bool = False
    ) -> CourierAssignment | None:
        query = select(CourierAssignment).where(
            CourierAssignment.shipment_id == shipment_id,
            CourierAssignment.status == AssignmentStatus.ACTIVE,
        )
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query.execution_options(populate_existing=True))

    async def list(
        self,
        *,
        shipment_id: uuid.UUID | None,
        courier_id: uuid.UUID | None,
        status: AssignmentStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[CourierAssignment], int]:
        filters = []
        if shipment_id:
            filters.append(CourierAssignment.shipment_id == shipment_id)
        if courier_id:
            filters.append(CourierAssignment.courier_id == courier_id)
        if status:
            filters.append(CourierAssignment.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(CourierAssignment).where(*filters)
        )
        rows = await self.session.scalars(
            select(CourierAssignment)
            .where(*filters)
            .order_by(CourierAssignment.assigned_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0
