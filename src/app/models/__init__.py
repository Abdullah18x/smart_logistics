"""Model registry.

Every model must be imported here so SQLAlchemy can resolve string-based
relationships and Alembic can see the full metadata.
"""

from app.models.address import Address
from app.models.base import Base
from app.models.courier import Courier
from app.models.courier_assignment import CourierAssignment
from app.models.idempotency_key import IdempotencyKey
from app.models.inventory_item import InventoryItem
from app.models.inventory_reservation import InventoryReservation
from app.models.package import Package
from app.models.password_reset_token import PasswordResetToken
from app.models.refresh_token import RefreshToken
from app.models.shipment import Shipment
from app.models.shipment_item import ShipmentItem
from app.models.sku import Sku
from app.models.stock_movement import StockMovement
from app.models.user import User
from app.models.user_warehouse_assignment import UserWarehouseAssignment
from app.models.warehouse import Warehouse
from app.models.warehouse_operating_hours import WarehouseOperatingHours
from app.models.warehouse_zone import WarehouseZone

__all__ = [
    "Address",
    "Base",
    "Courier",
    "CourierAssignment",
    "IdempotencyKey",
    "InventoryItem",
    "InventoryReservation",
    "Package",
    "PasswordResetToken",
    "RefreshToken",
    "Shipment",
    "ShipmentItem",
    "Sku",
    "StockMovement",
    "User",
    "UserWarehouseAssignment",
    "Warehouse",
    "WarehouseOperatingHours",
    "WarehouseZone",
]
