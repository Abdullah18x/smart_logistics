"""Contracts for delivery addresses."""

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.constants.formats import DEFAULT_COUNTRY_CODE, PHONE_PATTERN

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
