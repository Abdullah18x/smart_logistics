"""Request and response contracts for the Courier service."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from courier.models import (
    AssignmentMethod,
    AssignmentStatus,
    Availability,
    CourierStatus,
    VehicleType,
)

PHONE_PATTERN = r"^\+?[0-9\s\-()]{7,32}$"
CODE_PATTERN = r"^[A-Z0-9\-]+$"

# --- profiles ---------------------------------------------------------------------


class CourierBase(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    phone: str = Field(max_length=32, pattern=PHONE_PATTERN)
    email: EmailStr | None = None
    vehicle_type: VehicleType
    vehicle_plate: str | None = Field(default=None, max_length=32)
    max_active_shipments: int = Field(ge=1, le=100, description="Capacity: concurrent shipments.")
    max_weight_g: int | None = Field(default=None, ge=1, description="Heaviest load, in grams.")
    home_city: str = Field(min_length=2, max_length=100)
    home_warehouse_id: uuid.UUID | None = Field(
        default=None, description="Preferred for automatic assignment from this warehouse."
    )


class CourierCreate(CourierBase):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "CR-0101",
                "full_name": "Usman Tariq",
                "phone": "+923001119999",
                "vehicle_type": "motorcycle",
                "vehicle_plate": "KHI-4471",
                "max_active_shipments": 8,
                "max_weight_g": 30000,
                "home_city": "Karachi",
            }
        }
    )

    code: str = Field(min_length=3, max_length=16, pattern=CODE_PATTERN)
    user_id: uuid.UUID | None = Field(
        default=None, description="The Identity account this partner signs in with, if any."
    )


class CourierUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32, pattern=PHONE_PATTERN)
    email: EmailStr | None = None
    vehicle_type: VehicleType | None = None
    vehicle_plate: str | None = Field(default=None, max_length=32)
    max_active_shipments: int | None = Field(default=None, ge=1, le=100)
    max_weight_g: int | None = Field(default=None, ge=1)
    home_city: str | None = Field(default=None, min_length=2, max_length=100)
    home_warehouse_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


class CourierStatusUpdate(BaseModel):
    status: CourierStatus
    reason: str | None = Field(default=None, max_length=500)


class AvailabilityUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"availability": "available"}})

    availability: Availability


class CourierRead(CourierBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    user_id: uuid.UUID | None
    status: CourierStatus
    availability: Availability
    active_assignments: int
    last_assigned_at: datetime | None
    availability_changed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# --- assignments ------------------------------------------------------------------


class AssignmentRequest(BaseModel):
    """Assign a courier to a shipment. Idempotent by ``shipment_id``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "shipment_id": "00000000-0000-0000-0000-000000000000",
                "warehouse_id": "00000000-0000-0000-0000-000000000000",
                "total_weight_g": 2400,
            }
        }
    )

    shipment_id: uuid.UUID
    warehouse_id: uuid.UUID = Field(description="Origin warehouse: couriers are matched by city.")
    courier_id: uuid.UUID | None = Field(
        default=None, description="Pick this courier (manual); omit to choose automatically."
    )
    total_weight_g: int | None = Field(
        default=None, ge=0, description="Excludes couriers whose max weight is lower."
    )
    reason: str | None = Field(default=None, max_length=500)


class ReassignRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"reason": "Courier's vehicle broke down"}}
    )

    courier_id: uuid.UUID | None = Field(
        default=None, description="New courier; omit to choose automatically (never the current)."
    )
    reason: str = Field(min_length=3, max_length=500)


class EndAssignmentRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    courier_id: uuid.UUID
    shipment_id: uuid.UUID
    warehouse_id: uuid.UUID
    status: AssignmentStatus
    method: AssignmentMethod
    assigned_by: uuid.UUID | None
    reason: str | None
    assigned_at: datetime
    ended_at: datetime | None
    end_reason: str | None


class AssignmentResult(BaseModel):
    assignment: AssignmentRead
    courier: CourierRead
