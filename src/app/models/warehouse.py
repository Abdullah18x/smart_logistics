"""The warehouse: a physical facility in the logistics network."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Float, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants.enums import WarehouseStatus, WarehouseType
from app.constants.formats import DEFAULT_COUNTRY_CODE, DEFAULT_GEOFENCE_RADIUS_M, DEFAULT_TIMEZONE
from app.models.base import (
    WAREHOUSE_SCHEMA,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.warehouse_operating_hours import WarehouseOperatingHours
    from app.models.warehouse_zone import WarehouseZone


class Warehouse(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A fulfilment centre, hub, depot or returns facility.

    The address is stored inline rather than referenced: a warehouse has exactly
    one, permanent address, so a shared address table would add a join for no
    benefit and a cross-schema foreign key this design forbids (ADR-008).
    """

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
        {"schema": WAREHOUSE_SCHEMA},
    )

    # Human-facing identifier used on labels and in operations chatter, e.g. "KHI-01".
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[WarehouseType] = mapped_column(
        pg_enum(WarehouseType, "warehouse_type", WAREHOUSE_SCHEMA),
        nullable=False,
        default=WarehouseType.FULFILLMENT_CENTER,
        server_default=WarehouseType.FULFILLMENT_CENTER.value,
    )
    status: Mapped[WarehouseStatus] = mapped_column(
        pg_enum(WarehouseStatus, "warehouse_status", WAREHOUSE_SCHEMA),
        nullable=False,
        default=WarehouseStatus.ACTIVE,
        server_default=WarehouseStatus.ACTIVE.value,
    )

    # Address
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    region: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default=DEFAULT_COUNTRY_CODE
    )

    # --- Geolocation and live tracking (ADR-015) ---
    # Centroid of the facility. Used for distance and routing calculations.
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    # Where a courier actually drives to. On a large industrial site the gate can
    # be hundreds of metres from the centroid, which is the difference between a
    # driver finding the dock and circling the perimeter.
    entrance_latitude: Mapped[float | None] = mapped_column(Float)
    entrance_longitude: Mapped[float | None] = mapped_column(Float)

    # Provider-stable reference (Google place_id, OSM node, Mapbox feature id).
    # Survives address typos and reformatting, so provider calls stay reliable.
    map_place_id: Mapped[str | None] = mapped_column(String(255))

    # Radius around the entrance that counts as "at the warehouse". Arrival and
    # departure events are derived by testing courier pings against this circle.
    geofence_radius_m: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_GEOFENCE_RADIUS_M,
        server_default=str(DEFAULT_GEOFENCE_RADIUS_M),
    )

    # When the coordinates were last resolved from the address, so stale
    # geocodes can be refreshed in bulk.
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Capacity and throughput, used by dispatch planning.
    capacity_units: Mapped[int] = mapped_column(Integer, nullable=False)
    max_daily_outbound: Mapped[int | None] = mapped_column(
        Integer, doc="Shipments per day this facility can dispatch."
    )

    # Operating hours are stored as local times; this is how they are interpreted.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=DEFAULT_TIMEZONE
    )

    contact_name: Mapped[str | None] = mapped_column(String(150))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(String(1000))

    zones: Mapped[list["WarehouseZone"]] = relationship(
        back_populates="warehouse",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="WarehouseZone.code",
    )
    operating_hours: Mapped[list["WarehouseOperatingHours"]] = relationship(
        back_populates="warehouse",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="WarehouseOperatingHours.day_of_week",
    )

    @property
    def navigation_point(self) -> tuple[float, float] | None:
        """Where a courier should be routed: the entrance if known, else the centroid."""
        if self.entrance_latitude is not None and self.entrance_longitude is not None:
            return self.entrance_latitude, self.entrance_longitude
        if self.latitude is not None and self.longitude is not None:
            return self.latitude, self.longitude
        return None

    @property
    def is_locatable(self) -> bool:
        """Whether this facility can participate in routing and geofencing."""
        return self.navigation_point is not None

    @property
    def is_operational(self) -> bool:
        """Only operational warehouses may reserve stock or dispatch."""
        return self.status is WarehouseStatus.ACTIVE

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Warehouse {self.code} {self.status}>"
