"""Warehouse business logic. Methods flush; the endpoint commits once."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from sl_platform.auth import Principal
from sl_platform.db import utcnow
from sl_platform.errors import NotFoundError, PermissionDeniedError
from sl_platform.roles import UserRole
from warehouse.events import WarehouseEvents, emit
from warehouse.models import (
    Warehouse,
    WarehouseOperatingHours,
    WarehouseStatus,
    WarehouseType,
    WarehouseZone,
)
from warehouse.repository import WarehouseRepository
from warehouse.schemas import (
    WarehouseCreate,
    WarehouseStatusUpdate,
    WarehouseUpdate,
    WarehouseZoneCreate,
    WarehouseZoneUpdate,
    WeeklyScheduleUpdate,
)


class WarehouseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = WarehouseRepository(session)

    # --- reads -----------------------------------------------------------------

    async def get(self, warehouse_id: uuid.UUID, *, principal: Principal) -> Warehouse:
        warehouse = await self.repo.get(warehouse_id)
        # An operator outside their scope gets "not found": whether a facility
        # exists is information they are not entitled to.
        if warehouse is None or not principal.may_touch_warehouse(warehouse_id):
            raise NotFoundError("Warehouse not found.")
        return warehouse

    async def list(
        self,
        *,
        principal: Principal,
        city: str | None = None,
        type: WarehouseType | None = None,
        status: WarehouseStatus | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Warehouse], int]:
        return await self.repo.list_warehouses(
            city=city,
            type=type,
            status=status,
            search=search,
            allowed_ids=principal.scoped_warehouse_ids,
            limit=limit,
            offset=offset,
        )

    async def locations(self, *, city: str | None) -> list[Warehouse]:
        """The courier navigation view: active facilities only."""
        rows, _ = await self.repo.list_warehouses(
            city=city, status=WarehouseStatus.ACTIVE, with_hours=True, limit=200
        )
        return rows

    # --- writes ----------------------------------------------------------------

    async def create(self, payload: WarehouseCreate) -> Warehouse:
        warehouse = Warehouse(**payload.model_dump(exclude={"zones", "operating_hours"}))
        if warehouse.latitude is not None and warehouse.longitude is not None:
            warehouse.geocoded_at = utcnow()
        self.session.add(warehouse)
        await self.session.flush()
        for zone in payload.zones:
            self.session.add(WarehouseZone(warehouse_id=warehouse.id, **zone.model_dump()))
        for day in payload.operating_hours:
            self.session.add(WarehouseOperatingHours(warehouse_id=warehouse.id, **day.model_dump()))
        await self.session.flush()
        emit(self.session, WarehouseEvents.CREATED, warehouse)
        return await self._reload(warehouse.id)

    async def update(
        self, warehouse_id: uuid.UUID, payload: WarehouseUpdate, *, principal: Principal
    ) -> Warehouse:
        warehouse = await self.get(warehouse_id, principal=principal)
        changes = payload.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(warehouse, field, value)
        if {"latitude", "longitude"} & changes.keys():
            warehouse.geocoded_at = utcnow()
        await self.session.flush()
        emit(self.session, WarehouseEvents.UPDATED, warehouse)
        return await self._reload(warehouse_id)

    async def change_status(
        self, warehouse_id: uuid.UUID, payload: WarehouseStatusUpdate, *, principal: Principal
    ) -> Warehouse:
        warehouse = await self.get(warehouse_id, principal=principal)
        previous = warehouse.status
        warehouse.status = payload.status
        await self.session.flush()
        emit(
            self.session,
            WarehouseEvents.STATUS_CHANGED,
            warehouse,
            previous_status=previous.value,
            reason=payload.reason,
        )
        return await self._reload(warehouse_id)

    async def delete(self, warehouse_id: uuid.UUID, *, principal: Principal) -> None:
        """Soft delete. Other services react to ``warehouse.deleted`` — the
        event replaces the cross-database cascade."""
        warehouse = await self.get(warehouse_id, principal=principal)
        warehouse.status = WarehouseStatus.INACTIVE
        warehouse.deleted_at = utcnow()
        warehouse.deleted_by = principal.user_id
        await self.session.flush()
        emit(self.session, WarehouseEvents.DELETED, warehouse)

    async def restore(self, warehouse_id: uuid.UUID) -> Warehouse:
        warehouse = await self.repo.get(warehouse_id, include_deleted=True)
        if warehouse is None:
            raise NotFoundError("Warehouse not found.")
        warehouse.deleted_at = None
        warehouse.deleted_by = None
        warehouse.status = WarehouseStatus.ACTIVE
        await self.session.flush()
        emit(self.session, WarehouseEvents.RESTORED, warehouse)
        return await self._reload(warehouse_id)

    # --- zones -----------------------------------------------------------------

    async def add_zone(
        self, warehouse_id: uuid.UUID, payload: WarehouseZoneCreate, *, principal: Principal
    ) -> WarehouseZone:
        await self._assert_can_manage(warehouse_id, principal)
        zone = WarehouseZone(warehouse_id=warehouse_id, **payload.model_dump())
        self.session.add(zone)
        await self.session.flush()
        return zone

    async def update_zone(
        self, zone_id: uuid.UUID, payload: WarehouseZoneUpdate, *, principal: Principal
    ) -> WarehouseZone:
        zone = await self.repo.get_zone(zone_id)
        if zone is None:
            raise NotFoundError("Zone not found.")
        await self._assert_can_manage(zone.warehouse_id, principal)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(zone, field, value)
        await self.session.flush()
        return zone

    async def delete_zone(self, zone_id: uuid.UUID, *, principal: Principal) -> None:
        zone = await self.repo.get_zone(zone_id)
        if zone is None:
            raise NotFoundError("Zone not found.")
        await self._assert_can_manage(zone.warehouse_id, principal)
        zone.is_active = False
        zone.deleted_at = utcnow()
        zone.deleted_by = principal.user_id
        await self.session.flush()

    # --- operating hours -------------------------------------------------------

    async def list_hours(
        self, warehouse_id: uuid.UUID, *, principal: Principal
    ) -> list[WarehouseOperatingHours]:
        if principal.role is not UserRole.COURIER:
            await self.get(warehouse_id, principal=principal)
        elif await self.repo.get(warehouse_id) is None:
            raise NotFoundError("Warehouse not found.")
        return await self.repo.list_hours(warehouse_id)

    async def replace_hours(
        self, warehouse_id: uuid.UUID, payload: WeeklyScheduleUpdate, *, principal: Principal
    ) -> list[WarehouseOperatingHours]:
        """The whole week at once: a missing day would be ambiguous."""
        await self._assert_can_manage(warehouse_id, principal)
        await self.repo.clear_hours(warehouse_id)
        for day in payload.days:
            self.session.add(WarehouseOperatingHours(warehouse_id=warehouse_id, **day.model_dump()))
        await self.session.flush()
        return await self.repo.list_hours(warehouse_id)

    # --- helpers ---------------------------------------------------------------

    async def _assert_can_manage(self, warehouse_id: uuid.UUID, principal: Principal) -> None:
        if await self.repo.get(warehouse_id) is None:
            raise NotFoundError("Warehouse not found.")
        if principal.is_admin:
            return
        if principal.role is UserRole.WAREHOUSE_OPERATOR and principal.may_touch_warehouse(
            warehouse_id
        ):
            return
        raise PermissionDeniedError("You may only manage warehouses assigned to you.")

    async def _reload(self, warehouse_id: uuid.UUID) -> Warehouse:
        warehouse = await self.repo.get(warehouse_id, include_deleted=True)
        assert warehouse is not None
        return warehouse
