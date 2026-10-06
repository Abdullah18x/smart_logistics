"""Catalog and stock positions: SKUs, opening positions, adjustments, listings."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from inventory.events import StockEvents
from inventory.models import (
    InventoryItem,
    InventoryReservation,
    ReservationStatus,
    Sku,
    StockMovement,
    StockMovementType,
)
from inventory.schemas import InventoryItemCreate, SkuCreate, SkuUpdate, StockAdjustment
from inventory.services.replicas import require_operational
from sl_platform.auth import Principal
from sl_platform.errors import NotFoundError, PermissionDeniedError
from sl_platform.events import Topics
from sl_platform.outbox import add_event
from sl_platform.roles import UserRole


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_skus(self, *, search: str | None, active_only: bool, limit: int, offset: int):
        filters = [Sku.deleted_at.is_(None)]
        if active_only:
            filters.append(Sku.is_active.is_(True))
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(Sku.code.ilike(pattern) | Sku.name.ilike(pattern))
        total = await self.session.scalar(select(func.count()).select_from(Sku).where(*filters))
        rows = await self.session.scalars(
            select(Sku).where(*filters).order_by(Sku.code).limit(limit).offset(offset)
        )
        return list(rows.all()), total or 0

    async def get_sku(self, sku_id: uuid.UUID) -> Sku:
        sku = await self.session.get(Sku, sku_id)
        if sku is None or sku.deleted_at is not None:
            raise NotFoundError("SKU not found.")
        return sku

    async def create_sku(self, payload: SkuCreate) -> Sku:
        sku = Sku(**payload.model_dump())
        self.session.add(sku)
        await self.session.flush()
        return sku

    async def update_sku(self, sku_id: uuid.UUID, payload: SkuUpdate) -> Sku:
        sku = await self.get_sku(sku_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(sku, field, value)
        await self.session.flush()
        return sku


class StockService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_items(
        self,
        *,
        principal: Principal,
        warehouse_id: uuid.UUID | None,
        sku_id: uuid.UUID | None,
        below_reorder_level: bool | None,
        in_stock_only: bool | None,
        limit: int,
        offset: int,
    ) -> tuple[list[InventoryItem], int]:
        filters = []
        if warehouse_id is not None:
            filters.append(InventoryItem.warehouse_id == warehouse_id)
        if sku_id is not None:
            filters.append(InventoryItem.sku_id == sku_id)
        if below_reorder_level:
            filters.append(InventoryItem.available_qty <= InventoryItem.reorder_level)
        if in_stock_only:
            filters.append(InventoryItem.available_qty > 0)
        if (scope := principal.scoped_warehouse_ids) is not None:
            filters.append(InventoryItem.warehouse_id.in_(scope))
        total = await self.session.scalar(
            select(func.count()).select_from(InventoryItem).where(*filters)
        )
        rows = await self.session.scalars(
            select(InventoryItem)
            .where(*filters)
            .order_by(InventoryItem.warehouse_id, InventoryItem.sku_id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), total or 0

    async def list_reservations(
        self,
        *,
        principal: Principal,
        shipment_id: uuid.UUID | None,
        status: ReservationStatus | None,
        limit: int,
    ) -> list[InventoryReservation]:
        filters = []
        if shipment_id is not None:
            filters.append(InventoryReservation.shipment_id == shipment_id)
        if status is not None:
            filters.append(InventoryReservation.status == status)
        if (scope := principal.scoped_warehouse_ids) is not None:
            filters.append(InventoryReservation.warehouse_id.in_(scope))
        rows = await self.session.scalars(
            select(InventoryReservation)
            .where(*filters)
            .order_by(InventoryReservation.created_at.desc())
            .limit(limit)
        )
        return list(rows.all())

    async def open_position(
        self, payload: InventoryItemCreate, *, principal: Principal
    ) -> InventoryItem:
        self._assert_manages(payload.warehouse_id, principal)
        await require_operational(self.session, payload.warehouse_id)
        sku = await self.session.get(Sku, payload.sku_id)
        if sku is None:
            raise NotFoundError("SKU not found.")
        item = InventoryItem(
            warehouse_id=payload.warehouse_id,
            sku_id=payload.sku_id,
            zone_id=payload.zone_id,
            on_hand_qty=payload.on_hand_qty,
            reorder_level=payload.reorder_level,
        )
        self.session.add(item)
        await self.session.flush()
        if payload.on_hand_qty:
            self.session.add(
                StockMovement(
                    inventory_item_id=item.id,
                    warehouse_id=item.warehouse_id,
                    sku_id=item.sku_id,
                    type=StockMovementType.INBOUND_RECEIPT,
                    quantity_delta=payload.on_hand_qty,
                    resulting_on_hand=payload.on_hand_qty,
                    reference_type="opening",
                    actor_id=principal.user_id,
                    notes="Opening stock",
                )
            )
            await self.session.flush()
        return item

    async def adjust(
        self, item_id: uuid.UUID, payload: StockAdjustment, *, principal: Principal
    ) -> InventoryItem:
        """A deliberate correction. Locks the row; the constraints refuse a
        result below what is already held."""
        item = await self.session.scalar(
            select(InventoryItem).where(InventoryItem.id == item_id).with_for_update()
        )
        if item is None or not principal.may_touch_warehouse(item.warehouse_id):
            raise NotFoundError("Stock position not found.")
        self._assert_manages(item.warehouse_id, principal)
        item.on_hand_qty += payload.quantity_delta
        item.version += 1
        self.session.add(
            StockMovement(
                inventory_item_id=item.id,
                warehouse_id=item.warehouse_id,
                sku_id=item.sku_id,
                type=payload.type,
                quantity_delta=payload.quantity_delta,
                resulting_on_hand=item.on_hand_qty,
                reference_type="adjustment",
                actor_id=principal.user_id,
                notes=payload.notes,
            )
        )
        await self.session.flush()
        add_event(
            self.session,
            topic=Topics.INVENTORY,
            event_type=StockEvents.ADJUSTED,
            key=item.id,
            payload={
                "inventory_item_id": item.id,
                "warehouse_id": item.warehouse_id,
                "sku_id": item.sku_id,
                "type": payload.type.value,
                "quantity_delta": payload.quantity_delta,
                "on_hand_qty": item.on_hand_qty,
            },
            producer="inventory",
        )
        return item

    async def movements(
        self, item_id: uuid.UUID, *, principal: Principal, limit: int
    ) -> list[StockMovement]:
        item = await self.session.get(InventoryItem, item_id)
        if item is None or not principal.may_touch_warehouse(item.warehouse_id):
            raise NotFoundError("Stock position not found.")
        rows = await self.session.scalars(
            select(StockMovement)
            .where(StockMovement.inventory_item_id == item_id)
            .order_by(StockMovement.occurred_at.desc())
            .limit(limit)
        )
        return list(rows.all())

    @staticmethod
    def _assert_manages(warehouse_id: uuid.UUID, principal: Principal) -> None:
        if principal.is_admin:
            return
        if (
            principal.role is UserRole.WAREHOUSE_OPERATOR
            and warehouse_id in principal.warehouse_ids
        ):
            return
        raise PermissionDeniedError("You may only manage stock in your own warehouses.")
