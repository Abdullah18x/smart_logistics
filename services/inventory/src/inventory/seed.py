"""Development catalog and stock: ``python -m inventory.seed``.

Warehouse ids are uuid5(code) — the Warehouse service's seeder uses the same
derivation, so positions line up across databases. The warehouse replica rows
seeded here are placeholders (dated 1970) that the first real
``warehouse.*`` event overwrites.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from inventory.config import settings
from inventory.db import database
from inventory.models import InventoryItem, Sku, StockMovement, StockMovementType, WarehouseRef

logger = logging.getLogger("inventory.seed")
SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def seed_id(entity: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"{entity}:{key}")


@dataclass(frozen=True)
class SeedSku:
    code: str
    name: str
    category: str
    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int
    unit_value: str
    is_fragile: bool = False
    is_hazmat: bool = False
    requires_cold_chain: bool = False


SEED_SKUS: tuple[SeedSku, ...] = (
    SeedSku(
        "SKU-ELEC-0001",
        "Wireless Keyboard",
        "electronics",
        650,
        440,
        140,
        35,
        "4500.00",
        is_fragile=True,
    ),
    SeedSku(
        "SKU-ELEC-0002",
        "27in Monitor",
        "electronics",
        5200,
        620,
        380,
        180,
        "48000.00",
        is_fragile=True,
    ),
    SeedSku("SKU-ELEC-0003", "USB-C Charger 65W", "electronics", 180, 90, 60, 30, "3200.00"),
    SeedSku(
        "SKU-HOME-0001",
        "Ceramic Dinner Set",
        "homeware",
        4200,
        400,
        400,
        260,
        "12500.00",
        is_fragile=True,
    ),
    SeedSku("SKU-HOME-0002", "Cotton Bed Sheet", "homeware", 1400, 350, 250, 90, "5800.00"),
    SeedSku("SKU-APPA-0001", "Denim Jacket", "apparel", 900, 380, 300, 80, "7200.00"),
    SeedSku("SKU-APPA-0002", "Running Shoes", "apparel", 1100, 330, 220, 130, "9800.00"),
    SeedSku("SKU-BOOK-0001", "Hardcover Novel", "books", 700, 240, 160, 40, "1800.00"),
    # Handling flags exist to constrain courier and zone selection later.
    SeedSku(
        "SKU-CHEM-0001",
        "Industrial Solvent 1L",
        "chemicals",
        1300,
        120,
        120,
        260,
        "6500.00",
        is_hazmat=True,
    ),
    SeedSku(
        "SKU-PHRM-0001",
        "Insulin Pack",
        "pharmaceuticals",
        300,
        180,
        120,
        90,
        "22000.00",
        requires_cold_chain=True,
        is_fragile=True,
    ),
)


#: Warehouses that carry stock (code, name, city, depth). The returns centre and
#: the facility under maintenance carry none.
STOCKED_WAREHOUSES = (
    ("KHI-01", "Karachi Central Fulfilment Centre", "Karachi", 800),
    ("LHE-01", "Lahore Distribution Hub", "Lahore", 500),
    ("ISB-01", "Islamabad Regional Depot", "Islamabad", 220),
)


async def seed() -> int:
    if settings.is_production:
        raise RuntimeError("Refusing to seed a production database.")
    created = 0
    async with database.scope() as session:
        existing_skus = set((await session.scalars(select(Sku.code))).all())
        for spec in SEED_SKUS:
            if spec.code in existing_skus:
                continue
            session.add(
                Sku(
                    id=seed_id("sku", spec.code),
                    code=spec.code,
                    name=spec.name,
                    category=spec.category,
                    weight_g=spec.weight_g,
                    length_mm=spec.length_mm,
                    width_mm=spec.width_mm,
                    height_mm=spec.height_mm,
                    is_fragile=spec.is_fragile,
                    is_hazmat=spec.is_hazmat,
                    requires_cold_chain=spec.requires_cold_chain,
                    unit_value=Decimal(spec.unit_value),
                    currency="PKR",
                )
            )
            created += 1
        await session.flush()

        pairs = {
            (r.warehouse_id, r.sku_id)
            for r in (
                await session.execute(select(InventoryItem.warehouse_id, InventoryItem.sku_id))
            ).all()
        }
        for code, name, city, base_qty in STOCKED_WAREHOUSES:
            warehouse_id = seed_id("warehouse", code)
            if await session.get(WarehouseRef, warehouse_id) is None:
                session.add(
                    WarehouseRef(
                        id=warehouse_id,
                        code=code,
                        name=name,
                        city=city,
                        status="active",
                        source_updated_at=EPOCH,
                    )
                )
            for index, spec in enumerate(SEED_SKUS):
                sku_id = seed_id("sku", spec.code)
                if (warehouse_id, sku_id) in pairs:
                    continue
                quantity = max(0, base_qty - index * 60)
                item_id = seed_id("inventory_item", f"{code}:{spec.code}")
                session.add(
                    InventoryItem(
                        id=item_id,
                        warehouse_id=warehouse_id,
                        sku_id=sku_id,
                        zone_id=seed_id("warehouse_zone", f"{code}:{code[:3]}-STO"),
                        on_hand_qty=quantity,
                        reorder_level=50,
                    )
                )
                if quantity:
                    await session.flush()
                    session.add(
                        StockMovement(
                            id=seed_id("stock_movement", f"{code}:{spec.code}:opening"),
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
    return created


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    logger.info("inventory: %d row(s) created", asyncio.run(seed()))


if __name__ == "__main__":
    main()
