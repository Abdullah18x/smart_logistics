"""Contracts for shipment endpoints."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.constants.enums import ServiceLevel, ShipmentPriority, ShipmentStatus
from app.constants.formats import DEFAULT_CURRENCY
from app.schemas.address import AddressCreate, AddressRead
from app.schemas.package import PackageCreate, PackageRead


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


class ShipmentFilter(BaseModel):
    """Query parameters for listing shipments."""

    status: ShipmentStatus | None = None
    service_level: ServiceLevel | None = None
    priority: ShipmentPriority | None = None
    origin_warehouse_id: uuid.UUID | None = None
    courier_id: uuid.UUID | None = None
    city: str | None = Field(default=None, max_length=100)
    search: str | None = Field(
        default=None, max_length=200, description="Matches reference or customer reference."
    )
    created_from: datetime | None = None
    created_to: datetime | None = None
    overdue_only: bool | None = Field(
        default=None, description="Past promised delivery and not yet delivered."
    )
