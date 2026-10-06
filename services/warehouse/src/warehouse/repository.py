"""Database access for warehouses, zones and hours. Flush only; endpoints commit."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from warehouse.models import (
    Warehouse,
    WarehouseOperatingHours,
    WarehouseStatus,
    WarehouseType,
    WarehouseZone,
)


class WarehouseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, warehouse_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Warehouse | None:
        """Loads zones (live only) and hours, refreshing them even if cached."""
        warehouse = await self.session.scalar(
            select(Warehouse)
            .where(Warehouse.id == warehouse_id)
            .options(
                selectinload(Warehouse.zones.and_(WarehouseZone.deleted_at.is_(None))),
                selectinload(Warehouse.operating_hours),
            )
            .execution_options(populate_existing=True)
        )
        if warehouse is None or (warehouse.deleted_at is not None and not include_deleted):
            return None
        return warehouse

    async def list_warehouses(
        self,
        *,
        city: str | None = None,
        type: WarehouseType | None = None,
        status: WarehouseStatus | None = None,
        search: str | None = None,
        allowed_ids: Sequence[uuid.UUID] | None = None,
        with_hours: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Warehouse], int]:
        filters = [Warehouse.deleted_at.is_(None)]
        if city:
            filters.append(Warehouse.city.ilike(f"%{city.strip()}%"))
        if type is not None:
            filters.append(Warehouse.type == type)
        if status is not None:
            filters.append(Warehouse.status == status)
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(or_(Warehouse.code.ilike(pattern), Warehouse.name.ilike(pattern)))
        if allowed_ids is not None:
            filters.append(Warehouse.id.in_(allowed_ids))
        total = await self.session.scalar(
            select(func.count()).select_from(Warehouse).where(*filters)
        )
        statement = (
            select(Warehouse).where(*filters).order_by(Warehouse.code).limit(limit).offset(offset)
        )
        if with_hours:
            statement = statement.options(selectinload(Warehouse.operating_hours))
        rows = await self.session.scalars(statement)
        return list(rows.all()), total or 0

    async def get_zone(self, zone_id: uuid.UUID) -> WarehouseZone | None:
        zone = await self.session.get(WarehouseZone, zone_id)
        return None if zone is None or zone.deleted_at is not None else zone

    async def list_hours(self, warehouse_id: uuid.UUID) -> list[WarehouseOperatingHours]:
        rows = await self.session.scalars(
            select(WarehouseOperatingHours)
            .where(WarehouseOperatingHours.warehouse_id == warehouse_id)
            .order_by(WarehouseOperatingHours.day_of_week)
            .execution_options(populate_existing=True)
        )
        return list(rows.all())

    async def clear_hours(self, warehouse_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(WarehouseOperatingHours).where(
                WarehouseOperatingHours.warehouse_id == warehouse_id
            )
        )
