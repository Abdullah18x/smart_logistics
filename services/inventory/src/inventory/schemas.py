"""Request and response contracts for the Inventory service."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from inventory.models import DEFAULT_CURRENCY, ReservationStatus, StockMovementType

SKU_CODE_PATTERN = r"^[A-Za-z0-9\-_]+$"


# --- catalog -------------------------------------------------------------------


class SkuBase(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=100)

    # Integer units: grams and millimetres, so summing many items cannot drift.
    weight_g: int = Field(gt=0, le=1_000_000, description="Unit weight in grams.")
    length_mm: int = Field(gt=0, le=100_000)
    width_mm: int = Field(gt=0, le=100_000)
    height_mm: int = Field(gt=0, le=100_000)

    is_fragile: bool = False
    is_hazmat: bool = False
    requires_cold_chain: bool = False

    unit_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default=DEFAULT_CURRENCY, min_length=3, max_length=3)

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value


class SkuCreate(SkuBase):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "SKU-ELEC-0042",
                "name": "Wireless Keyboard",
                "category": "electronics",
                "weight_g": 650,
                "length_mm": 440,
                "width_mm": 140,
                "height_mm": 35,
                "is_fragile": True,
                "unit_value": "4500.00",
                "currency": "PKR",
            }
        }
    )

    code: str = Field(min_length=2, max_length=64, pattern=SKU_CODE_PATTERN)

    @field_validator("code", mode="before")
    @classmethod
    def upper_code(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value


class SkuUpdate(BaseModel):
    """Partial update. ``code`` is immutable — it is referenced by shipped items."""

    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=100)
    weight_g: int | None = Field(default=None, gt=0, le=1_000_000)
    length_mm: int | None = Field(default=None, gt=0, le=100_000)
    width_mm: int | None = Field(default=None, gt=0, le=100_000)
    height_mm: int | None = Field(default=None, gt=0, le=100_000)
    is_fragile: bool | None = None
    is_hazmat: bool | None = None
    requires_cold_chain: bool | None = None
    unit_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    is_active: bool | None = None


class SkuRead(SkuBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SkuSummary(BaseModel):
    """Compact reference embedded in inventory and shipment responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    weight_g: int


# --- stock -----------------------------------------------------------------------


class InventoryItemCreate(BaseModel):
    """Opens a stock position for a SKU at a warehouse."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "warehouse_id": "00000000-0000-0000-0000-000000000000",
                "sku_id": "00000000-0000-0000-0000-000000000000",
                "on_hand_qty": 500,
                "reorder_level": 50,
            }
        }
    )

    warehouse_id: uuid.UUID
    sku_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    on_hand_qty: int = Field(default=0, ge=0)
    reorder_level: int = Field(default=0, ge=0)


class InventoryItemUpdate(BaseModel):
    """Only placement and the reorder threshold are directly editable.

    Quantities are never set by hand — they change through stock adjustments so
    that every movement leaves a ledger entry.
    """

    zone_id: uuid.UUID | None = None
    reorder_level: int | None = Field(default=None, ge=0)


class StockAdjustment(BaseModel):
    """A deliberate correction to on-hand stock: a count, receipt or write-off."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "inbound_receipt",
                "quantity_delta": 250,
                "notes": "PO-2026-0912 received",
            }
        }
    )

    type: StockMovementType
    quantity_delta: int = Field(
        description="Signed. Positive adds stock, negative removes it. Never zero."
    )
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def non_zero(self) -> "StockAdjustment":
        if self.quantity_delta == 0:
            raise ValueError("quantity_delta must not be zero.")
        return self


class InventoryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    warehouse_id: uuid.UUID
    sku_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    on_hand_qty: int
    reserved_qty: int
    available_qty: int = Field(description="on_hand minus reserved. Maintained by the database.")
    reorder_level: int
    version: int
    created_at: datetime
    updated_at: datetime


class InventoryItemDetail(InventoryItemRead):
    """Stock level with the SKU resolved, for warehouse screens."""

    sku: SkuSummary | None = None


class ReservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    inventory_item_id: uuid.UUID
    shipment_id: uuid.UUID
    warehouse_id: uuid.UUID
    sku_id: uuid.UUID
    quantity: int
    status: ReservationStatus
    expires_at: datetime | None = None
    confirmed_at: datetime | None = None
    committed_at: datetime | None = None
    released_at: datetime | None = None
    release_reason: str | None = None
    created_at: datetime


class StockMovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    inventory_item_id: uuid.UUID
    warehouse_id: uuid.UUID
    sku_id: uuid.UUID
    type: StockMovementType
    quantity_delta: int
    resulting_on_hand: int
    reference_type: str | None = None
    reference_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    notes: str | None = None
    occurred_at: datetime


# --- reservations (called by Shipment) -------------------------------------------


class ReservationLineRequest(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(gt=0, le=100_000)


class ReservationRequest(BaseModel):
    """Hold stock for one shipment. ``shipment_id`` is the idempotency key:
    repeating the same request returns the existing hold."""

    shipment_id: uuid.UUID
    warehouse_id: uuid.UUID
    lines: list[ReservationLineRequest] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_skus(self) -> "ReservationRequest":
        skus = [line.sku_id for line in self.lines]
        if len(set(skus)) != len(skus):
            raise ValueError("Each SKU may appear only once.")
        return self


class ReservedLine(BaseModel):
    """A held line plus the SKU facts Shipment snapshots onto its items."""

    sku_id: uuid.UUID
    quantity: int
    sku_code: str
    sku_name: str
    unit_weight_g: int
    unit_value: Decimal | None = None
    currency: str


class ReservationResult(BaseModel):
    shipment_id: uuid.UUID
    warehouse_id: uuid.UUID
    status: ReservationStatus
    confirmed: bool
    expires_at: datetime | None = None
    lines: list[ReservedLine]


class ReleaseRequest(BaseModel):
    reason: str = Field(default="Released by request", max_length=255)
