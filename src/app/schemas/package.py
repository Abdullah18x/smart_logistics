"""Contracts for parcels."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import PackageStatus


class PackageCreate(BaseModel):
    """A parcel to be packed. The barcode is issued by the system, not the client."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "weight_g": 1200,
                "length_mm": 400,
                "width_mm": 300,
                "height_mm": 200,
            }
        }
    )

    weight_g: int = Field(gt=0, le=1_000_000)
    length_mm: int | None = Field(default=None, gt=0, le=100_000)
    width_mm: int | None = Field(default=None, gt=0, le=100_000)
    height_mm: int | None = Field(default=None, gt=0, le=100_000)


class PackageUpdate(BaseModel):
    weight_g: int | None = Field(default=None, gt=0, le=1_000_000)
    length_mm: int | None = Field(default=None, gt=0, le=100_000)
    width_mm: int | None = Field(default=None, gt=0, le=100_000)
    height_mm: int | None = Field(default=None, gt=0, le=100_000)
    status: PackageStatus | None = None


class PackageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    sequence_no: int
    barcode: str
    status: PackageStatus
    weight_g: int
    length_mm: int | None = None
    width_mm: int | None = None
    height_mm: int | None = None
    label_url: str | None = None
    label_generated_at: datetime | None = None
    packed_at: datetime | None = None
