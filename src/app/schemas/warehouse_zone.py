"""Contracts for warehouse zones."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import ZoneType


class WarehouseZoneBase(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=16,
        pattern=r"^[A-Za-z0-9\-]+$",
        description="Unique within its warehouse, e.g. 'A1'. Stored uppercase.",
    )
    name: str = Field(min_length=2, max_length=150)
    type: ZoneType = ZoneType.STORAGE
    capacity_units: int = Field(gt=0, description="Storage units this zone can hold.")

    # "before" so the value is cleaned prior to the pattern constraint.
    @field_validator("code", mode="before")
    @classmethod
    def upper_code(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value


class WarehouseZoneCreate(WarehouseZoneBase):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "A1",
                "name": "Ambient Storage A1",
                "type": "storage",
                "capacity_units": 5000,
            }
        }
    )


class WarehouseZoneUpdate(BaseModel):
    """Partial update. ``code`` is immutable — it appears on physical signage."""

    name: str | None = Field(default=None, min_length=2, max_length=150)
    type: ZoneType | None = None
    capacity_units: int | None = Field(default=None, gt=0)
    is_active: bool | None = None


class WarehouseZoneRead(WarehouseZoneBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    warehouse_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
