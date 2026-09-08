"""Delivery addresses."""

from sqlalchemy import CheckConstraint, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SHIPMENT_SCHEMA, Base, TimestampMixin, UUIDPrimaryKeyMixin


class Address(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Where a shipment is going.

    Stored per shipment rather than per customer: an address is a snapshot of
    where the parcel was actually sent. Editing a customer's saved address must
    never rewrite the destination of a parcel already delivered.
    """

    __tablename__ = "addresses"
    __table_args__ = (
        Index("ix_addresses_city", "city"),
        CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90)", name="latitude_range"),
        CheckConstraint(
            "longitude IS NULL OR (longitude BETWEEN -180 AND 180)", name="longitude_range"
        ),
        {"schema": SHIPMENT_SCHEMA},
    )

    contact_name: Mapped[str] = mapped_column(String(150), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(320))

    line1: Mapped[str] = mapped_column(String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, server_default="PK")

    # Geolocation for routing and geofenced delivery confirmation (ADR-015).
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    map_place_id: Mapped[str | None] = mapped_column(String(255))

    #: Free text from the customer, e.g. "gate code 4821, leave with security".
    delivery_instructions: Mapped[str | None] = mapped_column(String(500))

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None
