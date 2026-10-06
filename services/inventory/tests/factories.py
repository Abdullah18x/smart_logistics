from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from inventory.models import InventoryItem, InventoryReservation, Sku, WarehouseRef


class Factory:
    def __init__(self, sessionmaker) -> None:
        self.sessionmaker = sessionmaker

    async def warehouse(self, status: str = "active") -> uuid.UUID:
        warehouse_id = uuid.uuid4()
        async with self.sessionmaker() as session:
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

    async def sku(self, *, active: bool = True) -> Sku:
        async with self.sessionmaker() as session:
            sku = Sku(
                code=f"SKU-{uuid.uuid4().hex[:8]}",
                name="Widget",
                weight_g=500,
                length_mm=10,
                width_mm=10,
                height_mm=10,
                unit_value=Decimal("100.00"),
                is_active=active,
            )
            session.add(sku)
            await session.commit()
            return sku

    async def stock(self, warehouse_id: uuid.UUID, sku: Sku, on_hand: int = 10) -> InventoryItem:
        async with self.sessionmaker() as session:
            item = InventoryItem(warehouse_id=warehouse_id, sku_id=sku.id, on_hand_qty=on_hand)
            session.add(item)
            await session.commit()
            return item

    async def item(self, item_id: uuid.UUID) -> InventoryItem:
        async with self.sessionmaker() as session:
            return await session.get(InventoryItem, item_id)

    async def age_holds(self, shipment_id: uuid.UUID, minutes: int = 60) -> None:
        """Push an unconfirmed hold's expiry into the past."""
        from sqlalchemy import update

        async with self.sessionmaker() as session:
            await session.execute(
                update(InventoryReservation)
                .where(
                    InventoryReservation.shipment_id == shipment_id,
                    InventoryReservation.expires_at.is_not(None),
                )
                .values(expires_at=datetime.now(UTC) - timedelta(minutes=minutes))
            )
            await session.commit()
