"""Opening stock positions, with a matching ledger entry for each."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import StockMovementType
from app.models.inventory_item import InventoryItem
from app.models.stock_movement import StockMovement
from app.seeders.base import Seeder, seed_id
from app.seeders.sku_seeder import SEED_SKUS
from app.seeders.warehouse_seeder import SEED_WAREHOUSES

#: Warehouses that carry stock, and how deep. The returns centre and the
#: facility under maintenance are deliberately excluded.
STOCKED_WAREHOUSES: dict[str, int] = {
    "KHI-01": 800,
    "LHE-01": 500,
    "ISB-01": 220,
}


class InventorySeeder(Seeder):
    name = "inventory"
    depends_on = ("warehouses", "skus")

    async def run(self, session: AsyncSession) -> int:
        existing_pairs = {
            (row.warehouse_id, row.sku_id)
            for row in (
                await session.execute(select(InventoryItem.warehouse_id, InventoryItem.sku_id))
            ).all()
        }

        created = 0
        for warehouse in SEED_WAREHOUSES:
            base_qty = STOCKED_WAREHOUSES.get(warehouse.code)
            if base_qty is None:
                continue
            warehouse_id = seed_id("warehouse", warehouse.code)

            for index, sku in enumerate(SEED_SKUS):
                sku_id = seed_id("sku", sku.code)
                if (warehouse_id, sku_id) in existing_pairs:
                    continue

                # Vary the depth so listings, reorder alerts and out-of-stock
                # paths all have data to exercise.
                quantity = max(0, base_qty - index * 60)
                item_id = seed_id("inventory_item", f"{warehouse.code}:{sku.code}")
                session.add(
                    InventoryItem(
                        id=item_id,
                        warehouse_id=warehouse_id,
                        sku_id=sku_id,
                        zone_id=seed_id(
                            "warehouse_zone", f"{warehouse.code}:{warehouse.code[:3]}-STO"
                        ),
                        on_hand_qty=quantity,
                        reserved_qty=0,
                        reorder_level=50,
                    )
                )
                if quantity:
                    # Every stock level must be explainable by the ledger.
                    session.add(
                        StockMovement(
                            id=seed_id("stock_movement", f"{warehouse.code}:{sku.code}:opening"),
                            inventory_item_id=item_id,
                            warehouse_id=warehouse_id,
                            sku_id=sku_id,
                            type=StockMovementType.INBOUND_RECEIPT,
                            quantity_delta=quantity,
                            resulting_on_hand=quantity,
                            reference_type="seed",
                            notes="Opening stock",
                        )
                    )
                created += 1

        await session.flush()
        return created
