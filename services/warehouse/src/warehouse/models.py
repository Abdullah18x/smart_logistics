"""Warehouse's tables. Other services hold warehouse ids, never these rows."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from enum import IntEnum, StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sl_platform.db import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin, new_base, pg_enum

Base = new_base()

DEFAULT_COUNTRY_CODE = "PK"
DEFAULT_TIMEZONE = "Asia/Karachi"
DEFAULT_GEOFENCE_RADIUS_M = 150


class WarehouseType(StrEnum):
    FULFILLMENT_CENTER = "fulfillment_center"
    DISTRIBUTION_HUB = "distribution_hub"
    REGIONAL_DEPOT = "regional_depot"
    RETURNS_CENTER = "returns_center"


class WarehouseStatus(StrEnum):
    """Only ACTIVE facilities may originate shipments or have stock reserved."""

    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    INACTIVE = "inactive"


class ZoneType(StrEnum):
    RECEIVING = "receiving"
    STORAGE = "storage"
    PICKING = "picking"
    PACKING = "packing"
    STAGING = "staging"
    DISPATCH = "dispatch"
    RETURNS = "returns"
    QUARANTINE = "quarantine"


class Weekday(IntEnum):
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6
    SUNDAY = 7


class Warehouse(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        Index("ix_warehouses_city_status", "city", "status"),
        Index(
            "ix_warehouses_active_city",
            "city",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_warehouses_type_status", "type", "status"),
        CheckConstraint("capacity_units > 0", name="capacity_positive"),
        CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90)", name="latitude_range"),
        CheckConstraint(
            "longitude IS NULL OR (longitude BETWEEN -180 AND 180)", name="longitude_range"
        ),
        CheckConstraint(
            "entrance_latitude IS NULL OR (entrance_latitude BETWEEN -90 AND 90)",
            name="entrance_latitude_range",
        ),
        CheckConstraint(
            "entrance_longitude IS NULL OR (entrance_longitude BETWEEN -180 AND 180)",
            name="entrance_longitude_range",
        ),
        CheckConstraint("geofence_radius_m > 0", name="geofence_radius_positive"),
    )

    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[WarehouseType] = mapped_column(
        pg_enum(WarehouseType, "warehouse_type"),
        nullable=False,
        default=WarehouseType.FULFILLMENT_CENTER,
        server_default=WarehouseType.FULFILLMENT_CENTER.value,
    )
    status: Mapped[WarehouseStatus] = mapped_column(
        pg_enum(WarehouseStatus, "warehouse_status"),
        nullable=False,
        default=WarehouseStatus.ACTIVE,
        server_default=WarehouseStatus.ACTIVE.value,
    )

    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    region: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, default=DEFAULT_COUNTRY_CODE, server_default=DEFAULT_COUNTRY_CODE
    )

    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    entrance_latitude: Mapped[float | None] = mapped_column(Float)
    entrance_longitude: Mapped[float | None] = mapped_column(Float)
    map_place_id: Mapped[str | None] = mapped_column(String(255))
    geofence_radius_m: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_GEOFENCE_RADIUS_M,
        server_default=str(DEFAULT_GEOFENCE_RADIUS_M),
    )
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    capacity_units: Mapped[int] = mapped_column(Integer, nullable=False)
    max_daily_outbound: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE
    )

    contact_name: Mapped[str | None] = mapped_column(String(150))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(String(1000))

    zones: Mapped[list[WarehouseZone]] = relationship(
        back_populates="warehouse",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="WarehouseZone.code",
    )
    operating_hours: Mapped[list[WarehouseOperatingHours]] = relationship(
        back_populates="warehouse",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="WarehouseOperatingHours.day_of_week",
    )

    @property
    def is_operational(self) -> bool:
        return self.status is WarehouseStatus.ACTIVE and self.deleted_at is None


class WarehouseZone(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "warehouse_zones"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "code", name="uq_warehouse_zone_code"),
        Index(
            "ix_zones_active_warehouse", "warehouse_id", postgresql_where=text("deleted_at IS NULL")
        ),
        CheckConstraint("capacity_units > 0", name="capacity_positive"),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[ZoneType] = mapped_column(
        pg_enum(ZoneType, "zone_type"),
        nullable=False,
        default=ZoneType.STORAGE,
        server_default=ZoneType.STORAGE.value,
    )
    capacity_units: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    warehouse: Mapped[Warehouse] = relationship(back_populates="zones", lazy="raise")


class WarehouseOperatingHours(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "warehouse_operating_hours"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "day_of_week", name="uq_warehouse_operating_day"),
        CheckConstraint("day_of_week BETWEEN 1 AND 7", name="day_of_week_range"),
        CheckConstraint(
            "(is_closed AND opens_at IS NULL AND closes_at IS NULL) OR "
            "(NOT is_closed AND opens_at IS NOT NULL AND closes_at IS NOT NULL "
            "AND closes_at > opens_at)",
            name="valid_window",
        ),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time)
    closes_at: Mapped[time | None] = mapped_column(Time)
    is_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    warehouse: Mapped[Warehouse] = relationship(back_populates="operating_hours", lazy="raise")


CONSTRAINT_MESSAGES = {
    "ix_warehouses_code": "A warehouse with this code already exists.",
    "uq_warehouse_zone_code": "This zone code is already used in this warehouse.",
    "uq_warehouse_operating_day": "This weekday already has opening hours.",
    "ck_warehouse_operating_hours_valid_window": (
        "A day must either be closed, or open with a closing time after its opening time."
    ),
    "ck_warehouses_capacity_positive": "Capacity must be greater than zero.",
    "ck_warehouse_zones_capacity_positive": "Zone capacity must be greater than zero.",
}
