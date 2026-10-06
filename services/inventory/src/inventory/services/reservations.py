"""Stock holds: reserve, confirm, commit, release, expire.

The consistency-critical path. Rules:

1. **Pessimistic locking, one lock order.** Every path locks
   ``inventory_items`` rows first, ordered by id, and only then the
   reservations on them. Two transactions can therefore never wait on each
   other in opposite orders — the deadlock in the monolith's version.
2. **Idempotent by shipment.** ``shipment_id`` identifies a hold. Repeating a
   reserve returns the existing hold; repeating commit or release is a no-op.
3. **Confirmed holds never expire.** Only an *unconfirmed* hold — one whose
   shipment was never recorded — is reclaimed, lazily when its stock is
   contended or by the periodic sweep. A dispatched shipment therefore always
   finds its stock still held (the monolith's "expired hold" bug).

Methods flush; the caller commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inventory.events import StockEvents, emit_stock_event
from inventory.models import (
    InventoryItem,
    InventoryReservation,
    ReservationStatus,
    Sku,
    StockMovement,
    StockMovementType,
)
from inventory.schemas import ReservationRequest, ReservationResult, ReservedLine
from inventory.services.replicas import require_operational
from sl_platform.db import utcnow
from sl_platform.errors import ConflictError, NotFoundError


class InsufficientStockError(ConflictError):
    """Not enough available stock to satisfy the request."""

    code = "insufficient_stock"


class ReservationMismatchError(ConflictError):
    """This shipment already holds different stock."""

    code = "reservation_mismatch"


class ReservationService:
    def __init__(self, session: AsyncSession, *, unconfirmed_hold: timedelta) -> None:
        self.session = session
        self.unconfirmed_hold = unconfirmed_hold

    # --- reserve -------------------------------------------------------------------

    async def reserve(self, request: ReservationRequest) -> tuple[ReservationResult, bool]:
        """Hold stock for every line or none. Returns (result, created)."""
        existing = await self._for_shipment(request.shipment_id)
        if existing:
            return await self._replay(request, existing), False

        await require_operational(self.session, request.warehouse_id)
        skus = await self._load_skus([line.sku_id for line in request.lines])
        items = await self._lock_items(request.warehouse_id, list(skus))
        await self._expire_unconfirmed(list(items.values()))

        missing = [skus[s].code for s in skus if s not in items]
        if missing:
            raise InsufficientStockError(
                f"No stock position at this warehouse for: {', '.join(sorted(missing))}"
            )
        shortfalls = [
            f"{skus[line.sku_id].code} (requested {line.quantity}, available {available})"
            for line in request.lines
            if (available := self._available(items[line.sku_id])) < line.quantity
        ]
        if shortfalls:
            raise InsufficientStockError("Insufficient stock for: " + "; ".join(shortfalls))

        expires_at = utcnow() + self.unconfirmed_hold
        holds = []
        for line in request.lines:
            item = items[line.sku_id]
            item.reserved_qty += line.quantity
            item.version += 1
            hold = InventoryReservation(
                inventory_item_id=item.id,
                shipment_id=request.shipment_id,
                warehouse_id=request.warehouse_id,
                sku_id=line.sku_id,
                quantity=line.quantity,
                status=ReservationStatus.HELD,
                expires_at=expires_at,
            )
            self.session.add(hold)
            holds.append(hold)
        await self.session.flush()
        emit_stock_event(
            self.session, StockEvents.RESERVED, request.shipment_id, request.warehouse_id, holds
        )
        return self._result(request.shipment_id, request.warehouse_id, holds, skus), True

    # --- confirm / commit / release ------------------------------------------------------

    async def confirm(self, shipment_id: uuid.UUID) -> int:
        """The shipment exists: its holds stop expiring."""
        holds = await self._lock_holds(shipment_id)
        now = utcnow()
        for hold in holds:
            if hold.confirmed_at is None:
                hold.confirmed_at = now
                hold.expires_at = None
        await self.session.flush()
        return len(holds)

    async def commit(self, shipment_id: uuid.UUID, *, actor_id: uuid.UUID | None = None) -> int:
        """Goods physically left: held quantity leaves both reserved and on hand."""
        holds = await self._lock_holds(shipment_id)
        if not holds:
            if await self._has_status(shipment_id, ReservationStatus.COMMITTED):
                return 0  # already committed: a retry
            raise ConflictError("This shipment holds no stock to commit.")
        items = await self._lock_item_ids([h.inventory_item_id for h in holds])
        now = utcnow()
        for hold in holds:
            item = items[hold.inventory_item_id]
            item.on_hand_qty -= hold.quantity
            item.reserved_qty -= hold.quantity
            item.version += 1
            self.session.add(
                StockMovement(
                    inventory_item_id=item.id,
                    warehouse_id=item.warehouse_id,
                    sku_id=item.sku_id,
                    type=StockMovementType.OUTBOUND_DISPATCH,
                    quantity_delta=-hold.quantity,
                    resulting_on_hand=item.on_hand_qty,
                    reference_type="shipment",
                    reference_id=shipment_id,
                    actor_id=actor_id,
                )
            )
            hold.status = ReservationStatus.COMMITTED
            hold.committed_at = now
        await self.session.flush()
        emit_stock_event(
            self.session, StockEvents.COMMITTED, shipment_id, holds[0].warehouse_id, holds
        )
        return len(holds)

    async def release(self, shipment_id: uuid.UUID, reason: str) -> int:
        """Give held stock back. Idempotent: releasing twice releases once."""
        holds = await self._lock_holds(shipment_id)
        if not holds:
            return 0
        items = await self._lock_item_ids([h.inventory_item_id for h in holds])
        now = utcnow()
        for hold in holds:
            self._release_one(hold, items[hold.inventory_item_id], reason, now)
        await self.session.flush()
        emit_stock_event(
            self.session,
            StockEvents.RELEASED,
            shipment_id,
            holds[0].warehouse_id,
            holds,
            reason=reason,
        )
        return len(holds)

    async def release_expired(self, warehouse_ids: Sequence[uuid.UUID] | None = None) -> int:
        """Sweep unconfirmed holds past expiry (scheduled on Celery beat in Week 3)."""
        statement = select(InventoryReservation.inventory_item_id).where(
            InventoryReservation.status == ReservationStatus.HELD,
            InventoryReservation.confirmed_at.is_(None),
            InventoryReservation.expires_at <= utcnow(),
        )
        if warehouse_ids is not None:
            statement = statement.where(InventoryReservation.warehouse_id.in_(warehouse_ids))
        item_ids = list(set((await self.session.scalars(statement)).all()))
        if not item_ids:
            return 0
        items = await self._lock_item_ids(item_ids)
        return await self._expire_unconfirmed(list(items.values()))

    # --- internals ---------------------------------------------------------------------

    @staticmethod
    def _available(item: InventoryItem) -> int:
        return item.on_hand_qty - item.reserved_qty

    def _release_one(
        self, hold: InventoryReservation, item: InventoryItem, reason: str, now
    ) -> None:
        item.reserved_qty -= hold.quantity
        item.version += 1
        hold.status = ReservationStatus.RELEASED
        hold.released_at = now
        hold.release_reason = reason

    async def _for_shipment(self, shipment_id: uuid.UUID) -> list[InventoryReservation]:
        rows = await self.session.scalars(
            select(InventoryReservation).where(InventoryReservation.shipment_id == shipment_id)
        )
        return list(rows.all())

    async def _has_status(self, shipment_id: uuid.UUID, status: ReservationStatus) -> bool:
        return (
            await self.session.scalar(
                select(InventoryReservation.id)
                .where(
                    InventoryReservation.shipment_id == shipment_id,
                    InventoryReservation.status == status,
                )
                .limit(1)
            )
        ) is not None

    async def _lock_holds(self, shipment_id: uuid.UUID) -> list[InventoryReservation]:
        """Lock a shipment's HELD reservations — items first, then holds."""
        item_ids = (
            await self.session.scalars(
                select(InventoryReservation.inventory_item_id).where(
                    InventoryReservation.shipment_id == shipment_id,
                    InventoryReservation.status == ReservationStatus.HELD,
                )
            )
        ).all()
        if not item_ids:
            return []
        await self._lock_item_ids(list(item_ids))
        rows = await self.session.scalars(
            select(InventoryReservation)
            .where(
                InventoryReservation.shipment_id == shipment_id,
                InventoryReservation.status == ReservationStatus.HELD,
            )
            .order_by(InventoryReservation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(rows.all())

    async def _lock_items(
        self, warehouse_id: uuid.UUID, sku_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, InventoryItem]:
        rows = await self.session.scalars(
            select(InventoryItem)
            .where(InventoryItem.warehouse_id == warehouse_id, InventoryItem.sku_id.in_(sku_ids))
            .order_by(InventoryItem.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {item.sku_id: item for item in rows.all()}

    async def _lock_item_ids(self, item_ids: list[uuid.UUID]) -> dict[uuid.UUID, InventoryItem]:
        rows = await self.session.scalars(
            select(InventoryItem)
            .where(InventoryItem.id.in_(item_ids))
            .order_by(InventoryItem.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {item.id: item for item in rows.all()}

    async def _expire_unconfirmed(self, items: list[InventoryItem]) -> int:
        """Release lapsed unconfirmed holds on rows this transaction already locked."""
        if not items:
            return 0
        by_id = {item.id: item for item in items}
        expired = (
            await self.session.scalars(
                select(InventoryReservation)
                .where(
                    InventoryReservation.inventory_item_id.in_(list(by_id)),
                    InventoryReservation.status == ReservationStatus.HELD,
                    InventoryReservation.confirmed_at.is_(None),
                    InventoryReservation.expires_at <= utcnow(),
                )
                .order_by(InventoryReservation.id)
                .with_for_update()
            )
        ).all()
        now = utcnow()
        by_shipment: dict[uuid.UUID, list[InventoryReservation]] = {}
        for hold in expired:
            self._release_one(hold, by_id[hold.inventory_item_id], "Unconfirmed hold expired", now)
            by_shipment.setdefault(hold.shipment_id, []).append(hold)
        if expired:
            await self.session.flush()
            for shipment_id, holds in by_shipment.items():
                emit_stock_event(
                    self.session,
                    StockEvents.RELEASED,
                    shipment_id,
                    holds[0].warehouse_id,
                    holds,
                    reason="Unconfirmed hold expired",
                )
        return len(expired)

    async def _load_skus(self, sku_ids: list[uuid.UUID]) -> dict[uuid.UUID, Sku]:
        rows = await self.session.scalars(
            select(Sku).where(Sku.id.in_(sku_ids), Sku.deleted_at.is_(None))
        )
        skus = {sku.id: sku for sku in rows.all()}
        missing = set(sku_ids) - skus.keys()
        if missing:
            raise NotFoundError(f"Unknown SKU(s): {', '.join(sorted(str(m) for m in missing))}")
        inactive = [s.code for s in skus.values() if not s.is_active]
        if inactive:
            raise ConflictError(f"Inactive SKU(s): {', '.join(sorted(inactive))}")
        return skus

    async def _replay(
        self, request: ReservationRequest, existing: list[InventoryReservation]
    ) -> ReservationResult:
        wanted = {(line.sku_id, line.quantity) for line in request.lines}
        held = {(r.sku_id, r.quantity) for r in existing}
        if wanted != held or existing[0].warehouse_id != request.warehouse_id:
            raise ReservationMismatchError("This shipment already holds different stock.")
        skus = await self._load_skus_any([r.sku_id for r in existing])
        return self._result(request.shipment_id, request.warehouse_id, existing, skus)

    async def _load_skus_any(self, sku_ids: list[uuid.UUID]) -> dict[uuid.UUID, Sku]:
        rows = await self.session.scalars(select(Sku).where(Sku.id.in_(sku_ids)))
        return {sku.id: sku for sku in rows.all()}

    @staticmethod
    def _result(
        shipment_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        holds: list[InventoryReservation],
        skus: dict[uuid.UUID, Sku],
    ) -> ReservationResult:
        statuses = {h.status for h in holds}
        status = statuses.pop() if len(statuses) == 1 else ReservationStatus.HELD
        return ReservationResult(
            shipment_id=shipment_id,
            warehouse_id=warehouse_id,
            status=status,
            confirmed=all(h.confirmed_at is not None for h in holds),
            expires_at=min((h.expires_at for h in holds if h.expires_at), default=None),
            lines=[
                ReservedLine(
                    sku_id=h.sku_id,
                    quantity=h.quantity,
                    sku_code=skus[h.sku_id].code,
                    sku_name=skus[h.sku_id].name,
                    unit_weight_g=skus[h.sku_id].weight_g,
                    unit_value=skus[h.sku_id].unit_value,
                    currency=skus[h.sku_id].currency,
                )
                for h in sorted(holds, key=lambda h: str(h.sku_id))
            ],
        )
