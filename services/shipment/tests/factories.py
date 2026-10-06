from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import update

from shipment.clients import Reservation, ReservedLine
from shipment.models import Shipment, WarehouseRef
from sl_platform.errors import RemoteError


class FakeInventory:
    """Stands in for the Inventory service's reservation API."""

    def __init__(self) -> None:
        self.reserved: dict[uuid.UUID, Reservation] = {}
        self.released: list[uuid.UUID] = []
        self.tokens: list[str] = []
        self.fail_with: Exception | None = None
        self.duplicate_lines = False

    async def reserve(self, *, token, shipment_id, warehouse_id, lines):
        self.tokens.append(token)
        if self.fail_with:
            raise self.fail_with
        if shipment_id in self.reserved:
            return self.reserved[shipment_id]
        reserved_lines = [
            ReservedLine(
                sku_id=sku,
                quantity=qty,
                sku_code=f"SKU-{str(sku)[:4]}",
                sku_name="Widget",
                unit_weight_g=250,
                unit_value=Decimal("10.00"),
                currency="PKR",
            )
            for sku, qty in lines
        ]
        if self.duplicate_lines:
            reserved_lines = reserved_lines * 2
        reservation = Reservation(
            shipment_id=shipment_id,
            warehouse_id=warehouse_id,
            status="held",
            confirmed=False,
            lines=reserved_lines,
        )
        self.reserved[shipment_id] = reservation
        return reservation

    async def release(self, *, token, shipment_id, reason):
        self.released.append(shipment_id)

    @staticmethod
    def insufficient() -> RemoteError:
        return RemoteError(409, "insufficient_stock", "Insufficient stock for: SKU-1")


async def add_warehouse(sessionmaker, status: str = "active") -> uuid.UUID:
    warehouse_id = uuid.uuid4()
    async with sessionmaker() as session:
        session.add(
            WarehouseRef(
                id=warehouse_id,
                code=f"W-{uuid.uuid4().hex[:5]}",
                name="Test",
                city="Karachi",
                status=status,
                source_updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return warehouse_id


async def assign_courier(sessionmaker, shipment_id, courier_id) -> None:
    """Courier assignment arrives with the Courier service (Phase 2)."""
    async with sessionmaker() as session:
        await session.execute(
            update(Shipment).where(Shipment.id == shipment_id).values(courier_id=courier_id)
        )
        await session.commit()
