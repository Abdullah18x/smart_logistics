"""Stock reservation, commitment and release.

This is the consistency-critical path (driver D2). Three rules govern it:

1. **Pessimistic locking.** Reserving takes ``SELECT ... FOR UPDATE`` on the
   affected rows. Two requests for the same stock serialise, and the second sees
   the first's effect rather than a stale read. Optimistic retries would be
   cheaper but would let a lost update oversell, which is unacceptable
   (ADR-013).
2. **Holds expire.** Stock reserved for an unconfirmed shipment is released
   after ``inventory_hold_minutes`` so it cannot be stranded forever.
3. **Expiry is lazy.** Rather than depend on a scheduled sweep, expired holds on
   the rows being locked are released inside the same transaction, immediately
   before availability is measured. A Celery reaper is added in Phase 3 for
   holds nobody happens to contend for; correctness does not depend on it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import ReservationStatus, StockMovementType
from app.constants.formats import RESERVATION_IDEMPOTENCY_KEY_TEMPLATE
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.models.inventory_item import InventoryItem
from app.models.inventory_reservation import InventoryReservation
from app.models.stock_movement import StockMovement


def _available(item: InventoryItem) -> int:
    """Availability computed in Python.

    Reading the generated ``available_qty`` column after modifying its inputs
    would force a database round trip mid-transaction.
    """
    return item.on_hand_qty - item.reserved_qty


class InsufficientStockError(ConflictError):
    """Not enough available stock to satisfy the request."""

    code = "insufficient_stock"


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reserve_for_shipment(
        self,
        *,
        shipment_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        lines: Sequence[tuple[uuid.UUID, int, str]],
    ) -> list[InventoryReservation]:
        """Hold stock for a shipment. ``lines`` is (sku_id, quantity, sku_code).

        Returns the reservations created. Raises ``InsufficientStockError`` if
        any line cannot be satisfied — the hold is all-or-nothing, since a
        partially reserved shipment cannot be dispatched anyway.
        """
        sku_ids = [sku_id for sku_id, _, _ in lines]
        items = await self._lock_items(warehouse_id, sku_ids)
        await self._release_expired(list(items.values()))

        missing = [code for sku_id, _, code in lines if sku_id not in items]
        if missing:
            raise InsufficientStockError(
                f"No stock position at this warehouse for: {', '.join(sorted(missing))}"
            )

        # Measure availability only after expired holds are released, so a
        # request is never refused because of stock nobody is really using.
        shortfalls = [
            f"{code} (requested {quantity}, available {_available(items[sku_id])})"
            for sku_id, quantity, code in lines
            if _available(items[sku_id]) < quantity
        ]
        if shortfalls:
            raise InsufficientStockError("Insufficient stock for: " + "; ".join(shortfalls))

        expires_at = datetime.now(UTC) + timedelta(minutes=settings.inventory_hold_minutes)
        reservations = []
        for sku_id, quantity, _ in lines:
            item = items[sku_id]
            item.reserved_qty += quantity
            item.version += 1
            reservation = InventoryReservation(
                inventory_item_id=item.id,
                shipment_id=shipment_id,
                warehouse_id=warehouse_id,
                sku_id=sku_id,
                quantity=quantity,
                status=ReservationStatus.HELD,
                expires_at=expires_at,
                # Deterministic, so a retried reserve cannot double-hold.
                idempotency_key=RESERVATION_IDEMPOTENCY_KEY_TEMPLATE.format(
                    shipment_id=shipment_id, sku_id=sku_id
                ),
            )
            self.session.add(reservation)
            reservations.append(reservation)

        await self.session.flush()
        return reservations

    async def commit_for_shipment(self, shipment_id: uuid.UUID) -> int:
        """Stock physically leaves the warehouse. Called when dispatch succeeds.

        Held quantity moves out of both ``reserved_qty`` and ``on_hand_qty``, and
        each line gets a ledger entry.
        """
        held = await self._held_reservations(shipment_id, lock=True)
        now = datetime.now(UTC)

        for reservation in held:
            item = await self._lock_item_by_id(reservation.inventory_item_id)
            item.on_hand_qty -= reservation.quantity
            item.reserved_qty -= reservation.quantity
            item.version += 1

            self.session.add(
                StockMovement(
                    inventory_item_id=item.id,
                    warehouse_id=item.warehouse_id,
                    sku_id=item.sku_id,
                    type=StockMovementType.OUTBOUND_DISPATCH,
                    quantity_delta=-reservation.quantity,
                    resulting_on_hand=item.on_hand_qty,
                    reference_type="shipment",
                    reference_id=shipment_id,
                )
            )
            reservation.status = ReservationStatus.COMMITTED
            reservation.committed_at = now

        await self.session.flush()
        return len(held)

    async def release_for_shipment(self, shipment_id: uuid.UUID, reason: str) -> int:
        """Give stock back. Called on cancellation or a failed dispatch."""
        held = await self._held_reservations(shipment_id, lock=True)
        now = datetime.now(UTC)

        for reservation in held:
            item = await self._lock_item_by_id(reservation.inventory_item_id)
            item.reserved_qty -= reservation.quantity
            item.version += 1
            reservation.status = ReservationStatus.RELEASED
            reservation.released_at = now
            reservation.release_reason = reason

        await self.session.flush()
        return len(held)

    async def release_expired(self, warehouse_id: uuid.UUID | None = None) -> int:
        """Sweep every hold past its expiry.

        Lazy release covers contended stock; this covers the rest. Exposed as an
        admin endpoint now, and scheduled on Celery beat in Phase 3.
        """
        statement = select(InventoryReservation).where(
            InventoryReservation.status == ReservationStatus.HELD,
            InventoryReservation.expires_at <= datetime.now(UTC),
        )
        if warehouse_id is not None:
            statement = statement.where(InventoryReservation.warehouse_id == warehouse_id)

        expired = list((await self.session.scalars(statement.with_for_update())).all())
        for reservation in expired:
            item = await self._lock_item_by_id(reservation.inventory_item_id)
            await self._expire(reservation, item)

        await self.session.flush()
        return len(expired)

    # --- internals ---------------------------------------------------------

    async def _lock_items(
        self, warehouse_id: uuid.UUID, sku_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, InventoryItem]:
        """Lock the affected stock rows.

        Ordered by id so concurrent multi-line requests always take locks in the
        same sequence — two shipments sharing two SKUs would otherwise deadlock.
        """
        rows = await self.session.scalars(
            select(InventoryItem)
            .where(
                InventoryItem.warehouse_id == warehouse_id,
                InventoryItem.sku_id.in_(sku_ids),
            )
            .order_by(InventoryItem.id)
            .with_for_update()
        )
        return {item.sku_id: item for item in rows.all()}

    async def _lock_item_by_id(self, item_id: uuid.UUID) -> InventoryItem:
        return await self.session.scalar(
            select(InventoryItem).where(InventoryItem.id == item_id).with_for_update()
        )

    async def _held_reservations(
        self, shipment_id: uuid.UUID, *, lock: bool = False
    ) -> list[InventoryReservation]:
        statement = select(InventoryReservation).where(
            InventoryReservation.shipment_id == shipment_id,
            InventoryReservation.status == ReservationStatus.HELD,
        )
        if lock:
            statement = statement.with_for_update()
        return list((await self.session.scalars(statement)).all())

    async def _release_expired(self, items: list[InventoryItem]) -> int:
        """Release expired holds on the rows already locked by this transaction."""
        if not items:
            return 0
        expired = await self.session.scalars(
            select(InventoryReservation)
            .where(
                InventoryReservation.inventory_item_id.in_([i.id for i in items]),
                InventoryReservation.status == ReservationStatus.HELD,
                InventoryReservation.expires_at <= datetime.now(UTC),
            )
            .with_for_update()
        )
        by_id = {item.id: item for item in items}
        count = 0
        for reservation in expired.all():
            await self._expire(reservation, by_id[reservation.inventory_item_id])
            count += 1
        if count:
            await self.session.flush()
        return count

    @staticmethod
    async def _expire(reservation: InventoryReservation, item: InventoryItem) -> None:
        item.reserved_qty -= reservation.quantity
        item.version += 1
        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = datetime.now(UTC)
        reservation.release_reason = "Hold expired"
