"""Contracts for the product catalog."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    currency: str = Field(default="PKR", min_length=3, max_length=3)

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

    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9\-_]+$")

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
