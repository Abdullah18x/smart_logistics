"""The courier: a fleet profile, distinct from the login identity."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants.enums import CourierAvailability, VehicleType
from app.models.base import (
    COURIER_SCHEMA,
    IDENTITY_SCHEMA,
    WAREHOUSE_SCHEMA,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.courier_assignment import CourierAssignment
    from app.models.user import User


class Courier(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Operational profile for someone who carries shipments.

    Separate from ``identity.users`` on purpose: that row is credentials and
    access, this row is fleet capability. Work is assigned to *this* entity, so
    a courier could later be an external partner with no login at all.
    """

    __tablename__ = "couriers"
    __table_args__ = (
        Index("ix_couriers_city_availability", "home_city", "availability_status"),
        Index(
            "ix_couriers_active_city",
            "home_city",
            "availability_status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_couriers_availability_load", "availability_status", "current_load"),
        CheckConstraint("capacity_kg > 0", name="capacity_positive"),
        CheckConstraint("max_daily_shipments > 0", name="max_daily_positive"),
        CheckConstraint("current_load >= 0", name="current_load_non_negative"),
        CheckConstraint("rating IS NULL OR (rating BETWEEN 0 AND 5)", name="rating_range"),
        CheckConstraint("total_deliveries >= 0", name="total_deliveries_non_negative"),
        CheckConstraint("failed_deliveries >= 0", name="failed_deliveries_non_negative"),
        {"schema": COURIER_SCHEMA},
    )

    #: 1:1 with the login. Nullable so a partner courier can exist without one,
    #: and SET NULL so removing an account leaves the fleet profile intact.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{IDENTITY_SCHEMA}.users.id", ondelete="SET NULL"), unique=True
    )

    #: Denormalised from the user, so dispatch listings need no cross-schema read.
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    employee_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)

    # --- Capability: can this courier physically take the shipment? ---
    vehicle_type: Mapped[VehicleType] = mapped_column(
        pg_enum(VehicleType, "vehicle_type", COURIER_SCHEMA),
        nullable=False,
        default=VehicleType.MOTORCYCLE,
        server_default=VehicleType.MOTORCYCLE.value,
    )
    vehicle_registration: Mapped[str | None] = mapped_column(String(32))
    capacity_kg: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)

    #: Mirror the SKU handling flags; a shipment cannot be offered to a courier
    #: who is not cleared for what it contains.
    can_handle_hazmat: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    can_handle_cold_chain: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    can_handle_fragile: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # --- Availability: should this courier be offered work now? ---
    availability_status: Mapped[CourierAvailability] = mapped_column(
        pg_enum(CourierAvailability, "courier_availability", COURIER_SCHEMA),
        nullable=False,
        default=CourierAvailability.OFF_DUTY,
        server_default=CourierAvailability.OFF_DUTY.value,
    )
    home_city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    #: Additional cities this courier serves, beyond their home city.
    service_cities: Mapped[list[str] | None] = mapped_column(ARRAY(String(100)))
    #: Warehouse they normally collect from.
    base_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{WAREHOUSE_SCHEMA}.warehouses.id", ondelete="SET NULL")
    )

    # --- Load: the utilisation numerator and denominator ---
    max_daily_shipments: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20, server_default=text("20")
    )
    current_load: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # --- Performance, used to rank candidates ---
    rating: Mapped[float | None] = mapped_column(Numeric(3, 2))
    total_deliveries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    failed_deliveries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # --- Last known position (ADR-015) ---
    # Denormalised from the MongoDB ping stream so "where is my courier now" is
    # a single indexed read rather than a time-series query.
    last_latitude: Mapped[float | None] = mapped_column(Float)
    last_longitude: Mapped[float | None] = mapped_column(Float)
    last_location_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    assignments: Mapped[list["CourierAssignment"]] = relationship(
        back_populates="courier", lazy="raise"
    )
    user: Mapped["User | None"] = relationship(lazy="raise")

    @property
    def remaining_capacity(self) -> int:
        return max(0, self.max_daily_shipments - self.current_load)

    @property
    def can_accept_work(self) -> bool:
        """Whether the assignment ranker should consider this courier at all."""
        return (
            self.is_active
            and self.availability_status
            in (CourierAvailability.AVAILABLE, CourierAvailability.ON_DUTY)
            and self.remaining_capacity > 0
        )

    @property
    def success_rate(self) -> float | None:
        if not self.total_deliveries:
            return None
        return round((self.total_deliveries - self.failed_deliveries) / self.total_deliveries, 4)

    def serves_city(self, city: str) -> bool:
        cities = {self.home_city.lower(), *(c.lower() for c in self.service_cities or [])}
        return city.lower() in cities

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Courier {self.employee_code} {self.availability_status}>"
