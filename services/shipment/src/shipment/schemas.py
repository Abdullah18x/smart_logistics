"""Request and response contracts for the Shipment service."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from shipment.models import (
    DEFAULT_COUNTRY_CODE,
    DEFAULT_CURRENCY,
    PackageStatus,
    ServiceLevel,
    ShipmentPriority,
    ShipmentStatus,
)

PHONE_PATTERN = r"^\+?[0-9\s\-()]{7,32}$"


# --- addresses -------------------------------------------------------------------

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class AddressBase(BaseModel):
    contact_name: str = Field(min_length=2, max_length=150)
    contact_phone: str = Field(max_length=32, pattern=PHONE_PATTERN)
    contact_email: EmailStr | None = None

    line1: str = Field(min_length=3, max_length=255)
    line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=2, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str = Field(default=DEFAULT_COUNTRY_CODE, min_length=2, max_length=2)

    latitude: Latitude | None = None
    longitude: Longitude | None = None
    map_place_id: str | None = Field(default=None, max_length=255)
    delivery_instructions: str | None = Field(default=None, max_length=500)

    @field_validator("country_code", mode="before")
    @classmethod
    def upper_country(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value


class AddressCreate(AddressBase):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "contact_name": "Ayesha Khan",
                "contact_phone": "+923001234567",
                "line1": "House 12, Street 4, DHA Phase 6",
                "city": "Karachi",
                "region": "Sindh",
                "postal_code": "75500",
                "country_code": "PK",
                "latitude": 24.8007,
                "longitude": 67.0611,
                "delivery_instructions": "Gate code 4821, leave with security",
            }
        }
    )


class AddressRead(AddressBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


# --- packages --------------------------------------------------------------------


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


# --- shipments -------------------------------------------------------------------


class ShipmentItemCreate(BaseModel):
    """A line on a new shipment. SKU attributes are snapshotted server-side."""

    sku_id: uuid.UUID
    quantity: int = Field(gt=0, le=100_000)


class ShipmentItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku_id: uuid.UUID
    quantity: int
    sku_code: str
    sku_name: str
    unit_weight_g: int
    unit_value: Decimal | None = None
    total_weight_g: int


class ShipmentCreate(BaseModel):
    """Creates a shipment in ``created`` status.

    The destination address is supplied inline rather than by id: an address is
    a snapshot of where this parcel was sent, and editing a customer's saved
    address must never rewrite a delivered shipment's destination.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "customer_reference": "ORD-2026-88123",
                "origin_warehouse_id": "00000000-0000-0000-0000-000000000000",
                "service_level": "express",
                "priority": "normal",
                "destination_address": {
                    "contact_name": "Ayesha Khan",
                    "contact_phone": "+923001234567",
                    "line1": "House 12, Street 4, DHA Phase 6",
                    "city": "Karachi",
                    "country_code": "PK",
                },
                "items": [{"sku_id": "00000000-0000-0000-0000-000000000000", "quantity": 2}],
                "special_instructions": "Call on arrival",
            }
        }
    )

    customer_reference: str | None = Field(default=None, max_length=100)
    origin_warehouse_id: uuid.UUID
    service_level: ServiceLevel = ServiceLevel.STANDARD
    priority: ShipmentPriority = ShipmentPriority.NORMAL

    destination_address: AddressCreate
    items: list[ShipmentItemCreate] = Field(min_length=1, max_length=200)
    packages: list[PackageCreate] = Field(default_factory=list, max_length=50)

    promised_delivery_at: datetime | None = Field(
        default=None, description="Left unset, it is derived from the service level."
    )
    declared_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default=DEFAULT_CURRENCY, min_length=3, max_length=3)
    special_instructions: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def unique_skus(self) -> "ShipmentCreate":
        skus = [item.sku_id for item in self.items]
        if len(set(skus)) != len(skus):
            raise ValueError("Each SKU may appear only once; combine the quantities.")
        return self


class ShipmentUpdate(BaseModel):
    """Editable only before dispatch.

    Status is absent by design: transitions go through dedicated endpoints so
    each can enforce its own rules and side effects.
    """

    customer_reference: str | None = Field(default=None, max_length=100)
    service_level: ServiceLevel | None = None
    priority: ShipmentPriority | None = None
    promised_delivery_at: datetime | None = None
    declared_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    special_instructions: str | None = Field(default=None, max_length=500)


class ShipmentStatusUpdate(BaseModel):
    """A validated lifecycle transition."""

    status: ShipmentStatus
    reason: str | None = Field(default=None, max_length=500)


class ShipmentCancel(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ShipmentSummary(BaseModel):
    """Row shape for listings and dashboards — no nested collections."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference_no: str
    customer_reference: str | None = None
    status: ShipmentStatus
    service_level: ServiceLevel
    priority: ShipmentPriority
    origin_warehouse_id: uuid.UUID
    origin_warehouse_code: str
    courier_id: uuid.UUID | None = None
    total_weight_g: int
    promised_delivery_at: datetime | None = None
    dispatched_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime


class ShipmentRead(ShipmentSummary):
    """Full representation with items, packages and destination."""

    destination_address: AddressRead
    items: list[ShipmentItemRead] = Field(default_factory=list)
    packages: list[PackageRead] = Field(default_factory=list)

    declared_value: Decimal | None = None
    currency: str
    picked_up_at: datetime | None = None
    cancelled_at: datetime | None = None
    delivery_attempt_count: int
    failure_reason: str | None = None
    special_instructions: str | None = None
    dispatch_workflow_id: str | None = None
    version: int
    updated_at: datetime


class StatusHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_status: str | None = None
    to_status: str
    actor_id: uuid.UUID | None = None
    actor_role: str | None = None
    reason: str | None = None
    occurred_at: datetime
