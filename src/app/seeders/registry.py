"""Ordered list of seeders to run.

Register each new seeder here. Order matters where one entity references
another; ``depends_on`` documents the reason.
"""

from app.seeders.base import Seeder
from app.seeders.courier_seeder import CourierSeeder
from app.seeders.inventory_seeder import InventorySeeder
from app.seeders.shipment_seeder import ShipmentSeeder
from app.seeders.sku_seeder import SkuSeeder
from app.seeders.user_seeder import UserSeeder
from app.seeders.warehouse_seeder import WarehouseSeeder

SEEDERS: tuple[Seeder, ...] = (
    WarehouseSeeder(),
    SkuSeeder(),
    # Warehouse assignments reference warehouses by their derived seed id.
    UserSeeder(),
    # Stock positions need both a warehouse and a SKU.
    InventorySeeder(),
    # Courier profiles link to the seeded courier logins.
    CourierSeeder(),
    # Shipments consume warehouses, SKUs and courier profiles.
    ShipmentSeeder(),
)
