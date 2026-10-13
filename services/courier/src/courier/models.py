"""Courier's tables: fleet profiles, shipment assignments and a warehouse replica."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sl_platform.db import (
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    new_base,
    pg_enum,
    utcnow,
)

Base = new_base()


class VehicleType(StrEnum):
    BICYCLE = "bicycle"
    MOTORCYCLE = "motorcycle"
    CAR = "car"
    VAN = "van"
    TRUCK = "truck"


class CourierStatus(StrEnum):
    """Whether the partner may work at all. Set by admins."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class Availability(StrEnum):
    """Whether the courier is taking work right now. Set by the courier."""

    OFFLINE = "offline"
    AVAILABLE = "available"
    ON_BREAK = "on_break"


class AssignmentStatus(StrEnum):
    ACTIVE = "active"
    #: Taken away before the job finished: reassigned, dispatch failed, cancelled.
    RELEASED = "released"
    #: The shipment reached a terminal state (delivered, returned).
    COMPLETED = "completed"


class AssignmentMethod(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class Courier(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A delivery partner's fleet profile.

    Separate from the login account: a partner can exist before (or without) an
    account, and vehicle, capacity and availability change with fleet operations,
    not with authentication. ``user_id`` links the profile to an Identity user;
    Identity mirrors the link as ``users.courier_id`` from ``courier.*`` events, so
    the courier's access token names this profile.

    ``active_assignments`` is the current load, maintained in the same
    transaction as every assignment change; the check constraint keeps it within
    capacity whatever the code above does.
    """

    __tablename__ = "couriers"
    __table_args__ = (
        Index(
            "ix_couriers_assignable",
            "home_city",
            "availability",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("max_active_shipments > 0", name="capacity_positive"),
        CheckConstraint(
            "active_assignments >= 0 AND active_assignments <= max_active_shipments",
            name="load_within_capacity",
        ),
        CheckConstraint("max_weight_g IS NULL OR max_weight_g > 0", name="weight_positive"),
    )

    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    vehicle_type: Mapped[VehicleType] = mapped_column(
        pg_enum(VehicleType, "vehicle_type"), nullable=False
    )
    vehicle_plate: Mapped[str | None] = mapped_column(String(32))
    max_active_shipments: Mapped[int] = mapped_column(Integer, nullable=False)
    max_weight_g: Mapped[int | None] = mapped_column(Integer)
    home_city: Mapped[str] = mapped_column(String(100), nullable=False)
    home_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[CourierStatus] = mapped_column(
        pg_enum(CourierStatus, "courier_status"),
        nullable=False,
        default=CourierStatus.ACTIVE,
        server_default=CourierStatus.ACTIVE.value,
    )
    availability: Mapped[Availability] = mapped_column(
        pg_enum(Availability, "courier_availability"),
        nullable=False,
        default=Availability.OFFLINE,
        server_default=Availability.OFFLINE.value,
    )
    active_assignments: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    availability_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    @property
    def is_assignable(self) -> bool:
        return (
            self.deleted_at is None
            and self.status is CourierStatus.ACTIVE
            and self.availability is Availability.AVAILABLE
            and self.active_assignments < self.max_active_shipments
        )


class CourierAssignment(UUIDPrimaryKeyMixin, Base):
    """Who carries which shipment, with its full history.

    A reassignment releases the old row and inserts a new one, so the table is
    the record of every hand-over. At most one row per shipment is ``active``
    (partial unique index), which is what makes "assign" safe to retry and
    impossible to double-book.
    """

    __tablename__ = "courier_assignments"
    __table_args__ = (
        Index(
            "uq_courier_assignments_active_shipment",
            "shipment_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_courier_assignments_courier_status", "courier_id", "status"),
        Index("ix_courier_assignments_shipment", "shipment_id", "assigned_at"),
    )

    courier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("couriers.id", ondelete="RESTRICT"), nullable=False
    )
    shipment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[AssignmentStatus] = mapped_column(
        pg_enum(AssignmentStatus, "assignment_status"),
        nullable=False,
        default=AssignmentStatus.ACTIVE,
        server_default=AssignmentStatus.ACTIVE.value,
    )
    method: Mapped[AssignmentMethod] = mapped_column(
        pg_enum(AssignmentMethod, "assignment_method"), nullable=False
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reason: Mapped[str | None] = mapped_column(String(500))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=text("now()")
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(500))

    courier: Mapped[Courier] = relationship(lazy="raise")


class WarehouseRef(Base):
    """Local, read-only copy of warehouse facts, kept current from events.

    Automatic assignment matches couriers to the warehouse's city without
    calling the Warehouse service.
    """

    __tablename__ = "warehouse_refs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=text("now()"),
    )


CONSTRAINT_MESSAGES = {
    "uq_couriers_code": "A courier with this code already exists.",
    "uq_couriers_user_id": "This user account is already linked to another courier.",
    "uq_courier_assignments_active_shipment": "This shipment already has an active courier.",
    "ck_couriers_load_within_capacity": "The courier is at capacity.",
    "ck_couriers_capacity_positive": "Capacity must be at least 1.",
}
