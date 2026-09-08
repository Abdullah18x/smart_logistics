"""Contracts for courier profiles."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import CourierAvailability, VehicleType

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class CourierBase(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    phone: str = Field(max_length=32, pattern=r"^\+?[0-9\s\-()]{7,32}$")

    vehicle_type: VehicleType = VehicleType.MOTORCYCLE
    vehicle_registration: str | None = Field(default=None, max_length=32)
    capacity_kg: Decimal = Field(gt=0, le=50000, max_digits=8, decimal_places=2)

    can_handle_hazmat: bool = False
    can_handle_cold_chain: bool = False
    can_handle_fragile: bool = True

    home_city: str = Field(min_length=2, max_length=100)
    service_cities: list[str] | None = Field(
        default=None, max_length=50, description="Additional cities served beyond the home city."
    )
    base_warehouse_id: uuid.UUID | None = None
    max_daily_shipments: int = Field(default=20, gt=0, le=500)


class CourierCreate(CourierBase):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "employee_code": "CR-0007",
                "full_name": "Ali Hassan",
                "phone": "+923001110007",
                "vehicle_type": "van",
                "vehicle_registration": "KHI-4821",
                "capacity_kg": "800.00",
                "home_city": "Karachi",
                "service_cities": ["Hyderabad"],
                "max_daily_shipments": 30,
            }
        }
    )

    employee_code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9\-]+$")
    #: Links to the login account. Omitted for partner couriers with no login.
    user_id: uuid.UUID | None = None

    @field_validator("employee_code", mode="before")
    @classmethod
    def upper_code(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value


class CourierUpdate(BaseModel):
    """Partial update. Availability and location have their own endpoints."""

    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    vehicle_type: VehicleType | None = None
    vehicle_registration: str | None = Field(default=None, max_length=32)
    capacity_kg: Decimal | None = Field(
        default=None, gt=0, le=50000, max_digits=8, decimal_places=2
    )
    can_handle_hazmat: bool | None = None
    can_handle_cold_chain: bool | None = None
    can_handle_fragile: bool | None = None
    home_city: str | None = Field(default=None, min_length=2, max_length=100)
    service_cities: list[str] | None = Field(default=None, max_length=50)
    base_warehouse_id: uuid.UUID | None = None
    max_daily_shipments: int | None = Field(default=None, gt=0, le=500)
    is_active: bool | None = None


class CourierAvailabilityUpdate(BaseModel):
    """Couriers set this themselves at the start and end of a shift."""

    availability_status: CourierAvailability


class CourierLocationUpdate(BaseModel):
    """A position ping.

    The raw stream is stored in MongoDB; this updates the denormalised
    last-known position used by 'where is my courier now' (ADR-015).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"latitude": 24.8607, "longitude": 67.0011, "accuracy_m": 12.5}
        }
    )

    latitude: Latitude
    longitude: Longitude
    accuracy_m: float | None = Field(default=None, ge=0, le=10000)
    recorded_at: datetime | None = Field(
        default=None, description="Device timestamp. Defaults to server time when omitted."
    )


class CourierRead(CourierBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None = None
    employee_code: str
    availability_status: CourierAvailability
    current_load: int
    remaining_capacity: int
    rating: Decimal | None = None
    total_deliveries: int
    failed_deliveries: int
    success_rate: float | None = None
    last_latitude: float | None = None
    last_longitude: float | None = None
    last_location_at: datetime | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CourierSummary(BaseModel):
    """Compact reference embedded in shipments and assignment responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_code: str
    full_name: str
    phone: str
    vehicle_type: VehicleType
    availability_status: CourierAvailability


class CourierFilter(BaseModel):
    city: str | None = Field(default=None, max_length=100)
    availability_status: CourierAvailability | None = None
    vehicle_type: VehicleType | None = None
    available_only: bool | None = Field(
        default=None, description="Active, on shift, and with remaining capacity."
    )
    search: str | None = Field(default=None, max_length=200)
