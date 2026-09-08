"""Persistence for warehouses, zones and operating hours."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.enums import WarehouseStatus, WarehouseType
from app.models.warehouse import Warehouse
from app.models.warehouse_operating_hours import WarehouseOperatingHours
from app.models.warehouse_zone import WarehouseZone
from app.repositories.base import BaseRepository


class WarehouseRepository(BaseRepository[Warehouse]):
    model = Warehouse

    async def get(self, entity_id: uuid.UUID, *, include_deleted: bool = False) -> Warehouse | None:
        """Fetch a facility together with the collections a detail response needs.

        Loaded explicitly rather than left to the mapper default for two reasons.
        A plain ``session.get`` returns an identity-map hit without running any
        loader, so a caller that already holds the row would get it with the
        collections unloaded — and touching them later raises ``MissingGreenlet``
        under asyncio. And soft-deleted zones must be filtered out here: the
        relationship itself is unfiltered, so without this a removed zone would
        keep appearing in the response even though the delete succeeded.
        """
        warehouse = await self.session.scalar(
            select(Warehouse)
            .where(Warehouse.id == entity_id)
            .options(
                selectinload(Warehouse.zones.and_(WarehouseZone.deleted_at.is_(None))),
                selectinload(Warehouse.operating_hours),
            )
            # Refresh the collections even when the row is already in the session,
            # so a zone added or removed moments ago is reflected.
            .execution_options(populate_existing=True)
        )
        if warehouse is None:
            return None
        if not include_deleted and warehouse.deleted_at is not None:
            return None
        return warehouse

    async def get_by_code(self, code: str) -> Warehouse | None:
        return await self.session.scalar(
            self.active_only(select(Warehouse).where(Warehouse.code == code.upper()))
        )

    async def code_exists(self, code: str) -> bool:
        return bool(
            await self.session.scalar(
                select(func.count()).select_from(Warehouse).where(Warehouse.code == code.upper())
            )
        )

    async def list_warehouses(
        self,
        *,
        city: str | None = None,
        type: WarehouseType | None = None,
        status: WarehouseStatus | None = None,
        search: str | None = None,
        # None means "no row restriction"; a sequence restricts to those ids.
        allowed_ids: Sequence[uuid.UUID] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Warehouse], int]:
        filters = []
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
            self.active_only(select(func.count()).select_from(Warehouse).where(*filters))
        )
        rows = await self.session.scalars(
            self.active_only(select(Warehouse).where(*filters))
            .order_by(Warehouse.code)
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), total or 0

    async def get_zone(self, zone_id: uuid.UUID) -> WarehouseZone | None:
        zone = await self.session.get(WarehouseZone, zone_id)
        return None if zone is None or zone.deleted_at is not None else zone

    async def zone_code_exists(self, warehouse_id: uuid.UUID, code: str) -> bool:
        return bool(
            await self.session.scalar(
                select(func.count())
                .select_from(WarehouseZone)
                .where(
                    WarehouseZone.warehouse_id == warehouse_id,
                    WarehouseZone.code == code.upper(),
                    WarehouseZone.deleted_at.is_(None),
                )
            )
        )

    async def list_hours(self, warehouse_id: uuid.UUID) -> list[WarehouseOperatingHours]:
        rows = await self.session.scalars(
            select(WarehouseOperatingHours)
            .where(WarehouseOperatingHours.warehouse_id == warehouse_id)
            .order_by(WarehouseOperatingHours.day_of_week)
        )
        return list(rows.all())

    async def clear_hours(self, warehouse_id: uuid.UUID) -> None:
        for row in await self.list_hours(warehouse_id):
            await self.session.delete(row)
        await self.session.flush()


class UserWarehouseScopeRepository:
    """Resolves which warehouses a user may act on."""

    def __init__(self, session) -> None:
        self.session = session

    async def warehouse_ids_for(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        from app.models.user_warehouse_assignment import UserWarehouseAssignment

        rows = await self.session.scalars(
            select(UserWarehouseAssignment.warehouse_id).where(
                UserWarehouseAssignment.user_id == user_id
            )
        )
        return list(rows.all())
