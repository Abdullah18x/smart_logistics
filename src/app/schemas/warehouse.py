"""Contracts for warehouse endpoints."""

import uuid
from datetime import datetime
from typing import Annotated
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.enums import WarehouseStatus, WarehouseType
from app.schemas.warehouse_operating_hours import OperatingHoursRead, OperatingHoursWrite
from app.schemas.warehouse_zone import WarehouseZoneCreate, WarehouseZoneRead

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class WarehouseBase(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    type: WarehouseType = WarehouseType.FULFILLMENT_CENTER

    address_line1: str = Field(min_length=3, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=2, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str = Field(
        default="PK", min_length=2, max_length=2, description="ISO 3166-1 alpha-2."
    )

    latitude: Latitude | None = Field(default=None, description="Facility centroid.")
    longitude: Longitude | None = None
    entrance_latitude: Latitude | None = Field(
        default=None, description="Gate or dock the courier drives to. Falls back to the centroid."
    )
    entrance_longitude: Longitude | None = None
    map_place_id: str | None = Field(
        default=None,
        max_length=255,
        description="Provider-stable place reference, e.g. a Google place_id or OSM node.",
    )
    geofence_radius_m: int = Field(
        default=150,
        gt=0,
        le=5000,
        description="Radius around the entrance that counts as 'at the warehouse'.",
    )

    capacity_units: int = Field(gt=0, description="Total storage units at this facility.")
    max_daily_outbound: int | None = Field(
        default=None, gt=0, description="Shipments per day this facility can dispatch."
    )
    timezone: str = Field(
        default="Asia/Karachi", description="IANA name. Operating hours are local to it."
    )

    contact_name: str | None = Field(default=None, max_length=150)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(
        default=None, max_length=32, pattern=r"^\+?[0-9\s\-()]{7,32}$"
    )
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("country_code", mode="before")
    @classmethod
    def upper_country(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        # Reject unknown zones here: operating hours are meaningless without one.
        if value not in available_timezones():
            raise ValueError(f"Unknown IANA timezone: {value}")
        return value


class WarehouseCreate(WarehouseBase):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "KHI-02",
                "name": "Karachi North Fulfilment Centre",
                "type": "fulfillment_center",
                "address_line1": "Plot 42, Korangi Industrial Area",
                "city": "Karachi",
                "region": "Sindh",
                "postal_code": "74900",
                "country_code": "PK",
                "latitude": 24.8607,
                "longitude": 67.0011,
                "entrance_latitude": 24.8611,
                "entrance_longitude": 67.0018,
                "geofence_radius_m": 150,
                "capacity_units": 50000,
                "max_daily_outbound": 4000,
                "timezone": "Asia/Karachi",
                "contact_name": "Imran Sheikh",
                "contact_email": "khi02@transfleet.com",
                "contact_phone": "+922135000002",
            }
        }
    )

    code: str = Field(
        min_length=2,
        max_length=16,
        pattern=r"^[A-Za-z0-9\-]+$",
        description="Globally unique facility code, e.g. 'KHI-01'. Stored uppercase.",
    )
    #: Optional in the same request, so a facility can be created ready to use.
    zones: list[WarehouseZoneCreate] = Field(default_factory=list, max_length=50)
    operating_hours: list[OperatingHoursWrite] = Field(default_factory=list, max_length=7)

    # "before" so the value is cleaned prior to the pattern constraint.
    @field_validator("code", mode="before")
    @classmethod
    def upper_code(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value


class WarehouseUpdate(BaseModel):
    """Partial update.

    ``code`` is immutable: it is printed on labels and referenced by operations,
    so renaming it would break external references. ``status`` has its own
    endpoint because taking a facility offline has side effects.
    """

    name: str | None = Field(default=None, min_length=2, max_length=150)
    type: WarehouseType | None = None
    address_line1: str | None = Field(default=None, min_length=3, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    entrance_latitude: Latitude | None = None
    entrance_longitude: Longitude | None = None
    map_place_id: str | None = Field(default=None, max_length=255)
    geofence_radius_m: int | None = Field(default=None, gt=0, le=5000)
    capacity_units: int | None = Field(default=None, gt=0)
    max_daily_outbound: int | None = Field(default=None, gt=0)
    timezone: str | None = None
    contact_name: str | None = Field(default=None, max_length=150)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=1000)


class WarehouseStatusUpdate(BaseModel):
    """Taking a facility out of service stops new dispatch from it."""

    status: WarehouseStatus
    reason: str | None = Field(default=None, max_length=500)


class WarehouseSummary(BaseModel):
    """Compact reference embedded in shipments, assignments and listings."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    city: str
    status: WarehouseStatus


class WarehouseLocation(BaseModel):
    """Navigation view for couriers.

    Deliberately narrow: everything a driver needs to reach the dock and nothing
    about capacity, throughput or internal layout.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    status: WarehouseStatus

    address_line1: str
    address_line2: str | None = None
    city: str
    region: str | None = None
    postal_code: str | None = None
    country_code: str

    latitude: float | None = None
    longitude: float | None = None
    entrance_latitude: float | None = None
    entrance_longitude: float | None = None
    map_place_id: str | None = None
    geofence_radius_m: int

    contact_phone: str | None = None
    timezone: str
    operating_hours: list[OperatingHoursRead] = Field(default_factory=list)


class WarehouseRead(WarehouseBase):
    """Full representation, without the nested collections."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    status: WarehouseStatus
    geocoded_at: datetime | None = Field(
        default=None, description="When the coordinates were last resolved from the address."
    )
    created_at: datetime
    updated_at: datetime


class WarehouseDetail(WarehouseRead):
    """Full representation including zones and the weekly schedule."""

    zones: list[WarehouseZoneRead] = Field(default_factory=list)
    operating_hours: list[OperatingHoursRead] = Field(default_factory=list)


class WarehouseFilter(BaseModel):
    """Query parameters for listing warehouses."""

    city: str | None = Field(default=None, max_length=100)
    type: WarehouseType | None = None
    status: WarehouseStatus | None = None
    search: str | None = Field(
        default=None, max_length=200, description="Matches code or name, case-insensitive."
    )
