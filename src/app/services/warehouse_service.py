"""Warehouse management business logic."""

# The service defines a method named `list`, which shadows the builtin inside
# the class body. Postponing annotation evaluation keeps `list[...]` hints valid.
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import UserRole, WarehouseStatus, WarehouseType
from app.core.access import AccessScope
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.warehouse import Warehouse
from app.models.warehouse_operating_hours import WarehouseOperatingHours
from app.models.warehouse_zone import WarehouseZone
from app.repositories.warehouse_repository import WarehouseRepository
from app.schemas.warehouse import WarehouseCreate, WarehouseStatusUpdate, WarehouseUpdate
from app.schemas.warehouse_operating_hours import WeeklyScheduleUpdate
from app.schemas.warehouse_zone import WarehouseZoneCreate, WarehouseZoneUpdate


class WarehouseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.warehouses = WarehouseRepository(session)

    # --- reads -------------------------------------------------------------

    async def get(self, warehouse_id: uuid.UUID, *, scope: AccessScope) -> Warehouse:
        warehouse = await self.warehouses.get(warehouse_id)
        if warehouse is None:
            raise NotFoundError("Warehouse not found.")
        # Operators are told "not found", not "forbidden": whether a facility
        # exists is itself information they are not entitled to.
        if scope.role is UserRole.WAREHOUSE_OPERATOR and not scope.may_touch_warehouse(
            warehouse_id
        ):
            raise NotFoundError("Warehouse not found.")
        return warehouse

    async def list(
        self,
        *,
        scope: AccessScope,
        city: str | None = None,
        type: WarehouseType | None = None,
        status: WarehouseStatus | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Warehouse], int]:
        allowed = scope.warehouse_ids if scope.role is UserRole.WAREHOUSE_OPERATOR else None
        # Couriers navigate to facilities, so they see only operational ones.
        if scope.role is UserRole.COURIER:
            status = status or WarehouseStatus.ACTIVE
        return await self.warehouses.list_warehouses(
            city=city,
            type=type,
            status=status,
            search=search,
            allowed_ids=allowed,
            limit=limit,
            offset=offset,
        )

    # --- writes ------------------------------------------------------------

    async def create(self, payload: WarehouseCreate) -> Warehouse:
        # No pre-check on the code: the unique index is authoritative, and a
        # check-then-insert is a race. A violation is translated by
        # ``core.db_errors`` into the same 409 a manual check would have raised.
        data = payload.model_dump(exclude={"zones", "operating_hours"})
        warehouse = Warehouse(**data)
        if warehouse.latitude is not None and warehouse.longitude is not None:
            warehouse.geocoded_at = datetime.now(UTC)
        await self.warehouses.add(warehouse)

        for zone in payload.zones:
            self.session.add(WarehouseZone(warehouse_id=warehouse.id, **zone.model_dump()))
        for day in payload.operating_hours:
            self.session.add(WarehouseOperatingHours(warehouse_id=warehouse.id, **day.model_dump()))

        await self.session.commit()
        return await self._reload(warehouse.id)

    async def update(
        self, warehouse_id: uuid.UUID, payload: WarehouseUpdate, *, scope: AccessScope
    ) -> Warehouse:
        warehouse = await self.get(warehouse_id, scope=scope)
        changes = payload.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(warehouse, field, value)
        # Coordinates supplied by hand count as freshly resolved.
        if {"latitude", "longitude"} & changes.keys():
            warehouse.geocoded_at = datetime.now(UTC)
        await self.session.commit()
        return await self._reload(warehouse_id)

    async def change_status(
        self, warehouse_id: uuid.UUID, payload: WarehouseStatusUpdate, *, scope: AccessScope
    ) -> Warehouse:
        """Taking a facility offline stops new dispatch from it, so it is
        admin-only even though operators manage the site day to day."""
        warehouse = await self.get(warehouse_id, scope=scope)
        warehouse.status = payload.status
        await self.session.commit()
        return await self._reload(warehouse_id)

    async def delete(self, warehouse_id: uuid.UUID, *, scope: AccessScope) -> None:
        """Soft delete.

        The row stays: shipments, stock and assignments reference it by foreign
        key, and history must keep resolving. It is also marked inactive so it
        drops out of dispatch selection immediately.
        """
        warehouse = await self.get(warehouse_id, scope=scope)
        warehouse.status = WarehouseStatus.INACTIVE
        await self.warehouses.soft_delete(warehouse, actor_id=scope.user_id)
        await self.session.commit()

    async def restore(self, warehouse_id: uuid.UUID, *, scope: AccessScope) -> Warehouse:
        """Undo a soft delete. Impossible with a hard delete, which is the point."""
        warehouse = await self.warehouses.get(warehouse_id, include_deleted=True)
        if warehouse is None:
            raise NotFoundError("Warehouse not found.")
        await self.warehouses.restore(warehouse)
        warehouse.status = WarehouseStatus.ACTIVE
        await self.session.commit()
        return await self._reload(warehouse_id)

    # --- zones -------------------------------------------------------------

    async def add_zone(
        self, warehouse_id: uuid.UUID, payload: WarehouseZoneCreate, *, scope: AccessScope
    ) -> WarehouseZone:
        await self._assert_can_manage(warehouse_id, scope=scope)
        # Uniqueness is enforced by uq_warehouse_zone_code, not by a pre-check.
        zone = WarehouseZone(warehouse_id=warehouse_id, **payload.model_dump())
        self.session.add(zone)
        await self.session.commit()
        return zone

    async def update_zone(
        self, zone_id: uuid.UUID, payload: WarehouseZoneUpdate, *, scope: AccessScope
    ) -> WarehouseZone:
        zone = await self.warehouses.get_zone(zone_id)
        if zone is None:
            raise NotFoundError("Zone not found.")
        await self._assert_can_manage(zone.warehouse_id, scope=scope)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(zone, field, value)
        await self.session.commit()
        return zone

    async def delete_zone(self, zone_id: uuid.UUID, *, scope: AccessScope) -> None:
        """Soft delete: stock positions reference zones by foreign key."""
        zone = await self.warehouses.get_zone(zone_id)
        if zone is None:
            raise NotFoundError("Zone not found.")
        await self._assert_can_manage(zone.warehouse_id, scope=scope)
        zone.is_active = False
        await self.warehouses.soft_delete(zone, actor_id=scope.user_id)
        await self.session.commit()

    # --- operating hours ---------------------------------------------------

    async def list_hours(
        self, warehouse_id: uuid.UUID, *, scope: AccessScope
    ) -> list[WarehouseOperatingHours]:
        await self.get(warehouse_id, scope=scope)
        return await self.warehouses.list_hours(warehouse_id)

    async def replace_hours(
        self, warehouse_id: uuid.UUID, payload: WeeklyScheduleUpdate, *, scope: AccessScope
    ) -> list[WarehouseOperatingHours]:
        """Replaces the whole week.

        A partial update is ambiguous — an absent day could mean "unchanged" or
        "closed" — so the client always sends the full schedule.
        """
        await self._assert_can_manage(warehouse_id, scope=scope)
        await self.warehouses.clear_hours(warehouse_id)
        for day in payload.days:
            self.session.add(WarehouseOperatingHours(warehouse_id=warehouse_id, **day.model_dump()))
        await self.session.commit()
        return await self.warehouses.list_hours(warehouse_id)

    # --- helpers -----------------------------------------------------------

    async def _assert_can_manage(self, warehouse_id: uuid.UUID, *, scope: AccessScope) -> None:
        """Admins manage any facility; operators only their own."""
        warehouse = await self.warehouses.get(warehouse_id)
        if warehouse is None:
            raise NotFoundError("Warehouse not found.")
        if scope.is_admin:
            return
        if scope.role is UserRole.WAREHOUSE_OPERATOR and scope.may_touch_warehouse(warehouse_id):
            return
        raise PermissionDeniedError("You may only manage warehouses assigned to you.")

    async def _reload(self, warehouse_id: uuid.UUID) -> Warehouse:
        # The repository re-reads the collections itself, so no refresh here —
        # a plain refresh would reload zones unfiltered and bring back deleted ones.
        warehouse = await self.warehouses.get(warehouse_id)
        if warehouse is None:  # pragma: no cover - just committed
            raise NotFoundError("Warehouse not found.")
        return warehouse
