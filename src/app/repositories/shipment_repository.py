"""Persistence for shipments and their parts."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.enums import ServiceLevel, ShipmentPriority, ShipmentStatus
from app.models.address import Address
from app.models.package import Package
from app.models.shipment import Shipment
from app.repositories.base import BaseRepository

#: Statuses that mean the shipment is still owed to the customer.
_OPEN_STATUSES = (
    ShipmentStatus.CREATED,
    ShipmentStatus.READY_FOR_DISPATCH,
    ShipmentStatus.DISPATCHING,
    ShipmentStatus.DISPATCHED,
    ShipmentStatus.PICKED_UP,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.OUT_FOR_DELIVERY,
    ShipmentStatus.DELIVERY_FAILED,
)


class ShipmentRepository(BaseRepository[Shipment]):
    model = Shipment

    async def get_with_detail(self, shipment_id: uuid.UUID) -> Shipment | None:
        return await self.session.scalar(
            select(Shipment)
            .where(Shipment.id == shipment_id)
            .options(
                selectinload(Shipment.items),
                selectinload(Shipment.packages),
                selectinload(Shipment.destination_address),
            )
        )

    async def get_by_reference(self, reference_no: str) -> Shipment | None:
        return await self.session.scalar(
            select(Shipment).where(Shipment.reference_no == reference_no.upper())
        )

    async def next_reference_no(self) -> str:
        """Draws from a Postgres sequence, so concurrent creates cannot collide."""
        value = await self.session.scalar(select(func.nextval("shipments.shipment_reference_seq")))
        return f"SL-{datetime.now(UTC).year}-{value:06d}"

    async def list_shipments(
        self,
        *,
        status: ShipmentStatus | None = None,
        service_level: ServiceLevel | None = None,
        priority: ShipmentPriority | None = None,
        origin_warehouse_id: uuid.UUID | None = None,
        courier_id: uuid.UUID | None = None,
        city: str | None = None,
        search: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        overdue_only: bool | None = None,
        # Row scoping. None means unrestricted.
        allowed_warehouse_ids: Sequence[uuid.UUID] | None = None,
        restrict_to_courier_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Shipment], int]:
        filters = []
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
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    Shipment.reference_no.ilike(pattern),
                    Shipment.customer_reference.ilike(pattern),
                )
            )
        if created_from is not None:
            filters.append(Shipment.created_at >= created_from)
        if created_to is not None:
            filters.append(Shipment.created_at <= created_to)
        if overdue_only:
            filters.extend(
                [
                    Shipment.promised_delivery_at < datetime.now(UTC),
                    Shipment.status.in_(_OPEN_STATUSES),
                ]
            )
        if allowed_warehouse_ids is not None:
            filters.append(Shipment.origin_warehouse_id.in_(allowed_warehouse_ids))
        if restrict_to_courier_id is not None:
            filters.append(Shipment.courier_id == restrict_to_courier_id)

        statement = select(Shipment).where(Shipment.deleted_at.is_(None), *filters)
        if city:
            statement = statement.join(
                Address, Address.id == Shipment.destination_address_id
            ).where(Address.city.ilike(f"%{city.strip()}%"))

        count_statement = (
            select(func.count())
            .select_from(Shipment)
            .where(Shipment.deleted_at.is_(None), *filters)
        )
        if city:
            count_statement = count_statement.join(
                Address, Address.id == Shipment.destination_address_id
            ).where(Address.city.ilike(f"%{city.strip()}%"))

        total = await self.session.scalar(count_statement)
        rows = await self.session.scalars(
            statement.order_by(Shipment.created_at.desc()).limit(limit).offset(offset)
        )
        return list(rows.all()), total or 0

    async def next_package_sequence(self, shipment_id: uuid.UUID) -> int:
        highest = await self.session.scalar(
            select(func.max(Package.sequence_no)).where(Package.shipment_id == shipment_id)
        )
        return (highest or 0) + 1
