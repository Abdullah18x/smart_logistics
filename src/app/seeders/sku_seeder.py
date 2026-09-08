"""Dummy product catalog."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sku import Sku
from app.seeders.base import Seeder, seed_id


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


class SkuSeeder(Seeder):
    name = "skus"

    async def run(self, session: AsyncSession) -> int:
        codes = [s.code for s in SEED_SKUS]
        existing = set((await session.scalars(select(Sku.code).where(Sku.code.in_(codes)))).all())

        created = 0
        for spec in SEED_SKUS:
            if spec.code in existing:
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
        return created
