"""Contracts for stock levels, reservations and movements."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.enums import ReservationStatus, StockMovementType
from app.schemas.sku import SkuSummary


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


class InventoryFilter(BaseModel):
    warehouse_id: uuid.UUID | None = None
    sku_id: uuid.UUID | None = None
    below_reorder_level: bool | None = Field(
        default=None, description="Only positions at or under their reorder threshold."
    )
    in_stock_only: bool | None = Field(
        default=None, description="Only positions with availability."
    )


class ReservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    inventory_item_id: uuid.UUID
    shipment_id: uuid.UUID
    warehouse_id: uuid.UUID
    sku_id: uuid.UUID
    quantity: int
    status: ReservationStatus
    expires_at: datetime
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
