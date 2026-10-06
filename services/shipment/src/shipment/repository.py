"""Database access for shipments. Flush only; endpoints commit."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shipment.models import (
    Address,
    Package,
    ServiceLevel,
    Shipment,
    ShipmentPriority,
    ShipmentStatus,
    ShipmentStatusHistory,
    shipment_reference_seq,
)
from shipment.state_machine import OPEN_STATES

SHIPMENT_REFERENCE_PREFIX = "SL-"


class ShipmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_with_detail(self, shipment_id: uuid.UUID) -> Shipment | None:
        return await self.session.scalar(
            select(Shipment)
            .where(Shipment.id == shipment_id, Shipment.deleted_at.is_(None))
            .options(
                selectinload(Shipment.items),
                selectinload(Shipment.packages),
                selectinload(Shipment.destination_address),
            )
            .execution_options(populate_existing=True)
        )

    async def exists(self, shipment_id: uuid.UUID) -> bool:
        return (
            await self.session.scalar(select(Shipment.id).where(Shipment.id == shipment_id))
        ) is not None

    async def next_reference_no(self) -> str:
        value = await self.session.scalar(select(shipment_reference_seq.next_value()))
        return f"{SHIPMENT_REFERENCE_PREFIX}{datetime.now(UTC).year}-{value:06d}"

    async def next_package_sequence(self, shipment_id: uuid.UUID) -> int:
        highest = await self.session.scalar(
            select(func.max(Package.sequence_no)).where(Package.shipment_id == shipment_id)
        )
        return (highest or 0) + 1

    async def history(self, shipment_id: uuid.UUID) -> list[ShipmentStatusHistory]:
        rows = await self.session.scalars(
            select(ShipmentStatusHistory)
            .where(ShipmentStatusHistory.shipment_id == shipment_id)
            .order_by(ShipmentStatusHistory.occurred_at, ShipmentStatusHistory.id)
        )
        return list(rows.all())

    async def list_shipments(
        self,
        *,
        status: ShipmentStatus | None = None,
        service_level: ServiceLevel | None = None,
        priority: ShipmentPriority | None = None,
        origin_warehouse_id: uuid.UUID | None = None,
        courier_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        city: str | None = None,
        search: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        overdue_only: bool | None = None,
        allowed_warehouse_ids: Sequence[uuid.UUID] | None = None,
        restrict_to_courier_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Shipment], int]:
        filters = [Shipment.deleted_at.is_(None)]
        if status is not None:
            filters.append(Shipment.status == status)
        if service_level is not None:
            filters.append(Shipment.service_level == service_level)
        if priority is not None:
            filters.append(Shipment.priority == priority)
        if origin_warehouse_id is not None:
            filters.append(Shipment.origin_warehouse_id == origin_warehouse_id)
        if courier_id is not None:
            filters.append(Shipment.courier_id == courier_id)
        if created_by is not None:
            filters.append(Shipment.created_by == created_by)
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    Shipment.reference_no.ilike(pattern), Shipment.customer_reference.ilike(pattern)
                )
            )
        if created_from is not None:
            filters.append(Shipment.created_at >= created_from)
        if created_to is not None:
            filters.append(Shipment.created_at <= created_to)
        if overdue_only:
            filters += [
                Shipment.promised_delivery_at < datetime.now(UTC),
                Shipment.status.in_(OPEN_STATES),
            ]
        if allowed_warehouse_ids is not None:
            filters.append(Shipment.origin_warehouse_id.in_(allowed_warehouse_ids))
        if restrict_to_courier_id is not None:
            filters.append(Shipment.courier_id == restrict_to_courier_id)

        def scoped(statement):
            statement = statement.where(*filters)
            if city:
                statement = statement.join(
                    Address, Address.id == Shipment.destination_address_id
                ).where(Address.city.ilike(f"%{city.strip()}%"))
            return statement

        total = await self.session.scalar(scoped(select(func.count()).select_from(Shipment)))
        rows = await self.session.scalars(
            scoped(select(Shipment))
            .order_by(Shipment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), total or 0
