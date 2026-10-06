"""Shipment's tables. Warehouses, couriers, users and SKUs are referenced by id only."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    UniqueConstraint,
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

DEFAULT_CURRENCY = "PKR"
DEFAULT_COUNTRY_CODE = "PK"

#: Concurrent creates draw distinct numbers without coordination.
shipment_reference_seq = Sequence("shipment_reference_seq", metadata=Base.metadata)


class ShipmentStatus(StrEnum):
    CREATED = "created"
    READY_FOR_DISPATCH = "ready_for_dispatch"
    DISPATCHING = "dispatching"
    DISPATCH_FAILED = "dispatch_failed"
    DISPATCHED = "dispatched"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"
    RETURN_INITIATED = "return_initiated"
    RETURNED = "returned"
    CANCELLED = "cancelled"


class ServiceLevel(StrEnum):
    ECONOMY = "economy"
    STANDARD = "standard"
    EXPRESS = "express"
    SAME_DAY = "same_day"


class ShipmentPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class PackageStatus(StrEnum):
    PENDING = "pending"
    PACKED = "packed"
    LABELLED = "labelled"
    DISPATCHED = "dispatched"


class Address(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A snapshot of where this parcel was sent — never a shared customer address."""

    __tablename__ = "addresses"
    __table_args__ = (
        Index("ix_addresses_city", "city"),
        CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90)", name="latitude_range"),
        CheckConstraint(
            "longitude IS NULL OR (longitude BETWEEN -180 AND 180)", name="longitude_range"
        ),
    )

    contact_name: Mapped[str] = mapped_column(String(150), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    line1: Mapped[str] = mapped_column(String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, default=DEFAULT_COUNTRY_CODE, server_default=DEFAULT_COUNTRY_CODE
    )
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    map_place_id: Mapped[str | None] = mapped_column(String(255))
    delivery_instructions: Mapped[str | None] = mapped_column(String(500))


class Shipment(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """The aggregate root.

    ``version`` is a real optimistic lock: every UPDATE carries
    ``WHERE version = <read value>``, so two concurrent status changes cannot
    both apply (the monolith incremented it by hand and checked nothing).
    """

    __tablename__ = "shipments"
    __table_args__ = (
        Index("ix_shipments_status_priority", "status", "priority"),
        Index(
            "ix_shipments_active_status",
            "status",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_shipments_warehouse_status", "origin_warehouse_id", "status"),
        Index("ix_shipments_courier_status", "courier_id", "status"),
        Index("ix_shipments_created_by", "created_by"),
        Index("ix_shipments_promised_delivery_at", "promised_delivery_at"),
        CheckConstraint("total_weight_g >= 0", name="weight_non_negative"),
        CheckConstraint("delivery_attempt_count >= 0", name="attempts_non_negative"),
    )

    reference_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    customer_reference: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[ShipmentStatus] = mapped_column(
        pg_enum(ShipmentStatus, "shipment_status"),
        nullable=False,
        default=ShipmentStatus.CREATED,
        server_default=ShipmentStatus.CREATED.value,
    )
    service_level: Mapped[ServiceLevel] = mapped_column(
        pg_enum(ServiceLevel, "service_level"),
        nullable=False,
        default=ServiceLevel.STANDARD,
        server_default=ServiceLevel.STANDARD.value,
    )
    priority: Mapped[ShipmentPriority] = mapped_column(
        pg_enum(ShipmentPriority, "shipment_priority"),
        nullable=False,
        default=ShipmentPriority.NORMAL,
        server_default=ShipmentPriority.NORMAL.value,
    )

    # References into other services' databases: plain ids, no foreign keys.
    origin_warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: Snapshot, so listings show the code without asking Warehouse.
    origin_warehouse_code: Mapped[str] = mapped_column(String(16), nullable=False)
    courier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    destination_address_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("addresses.id", ondelete="RESTRICT"), nullable=False
    )

    total_weight_g: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    declared_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default=DEFAULT_CURRENCY, server_default=DEFAULT_CURRENCY
    )

    promised_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    delivery_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    special_instructions: Mapped[str | None] = mapped_column(String(500))
    dispatch_workflow_id: Mapped[str | None] = mapped_column(String(255))

    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": version}

    destination_address: Mapped[Address] = relationship(lazy="raise")
    items: Mapped[list[ShipmentItem]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="ShipmentItem.sku_code",
    )
    packages: Mapped[list[Package]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="Package.sequence_no",
    )


class ShipmentItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A line. SKU facts are snapshots returned by Inventory when stock was held."""

    __tablename__ = "shipment_items"
    __table_args__ = (
        UniqueConstraint("shipment_id", "sku_id", name="uq_shipment_item_sku"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False)
    sku_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_weight_g: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    shipment: Mapped[Shipment] = relationship(back_populates="items", lazy="raise")

    @property
    def total_weight_g(self) -> int:
        return self.quantity * self.unit_weight_g


class Package(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "packages"
    __table_args__ = (
        UniqueConstraint("shipment_id", "sequence_no", name="uq_package_sequence"),
        CheckConstraint("weight_g > 0", name="weight_positive"),
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    barcode: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    status: Mapped[PackageStatus] = mapped_column(
        pg_enum(PackageStatus, "package_status"),
        nullable=False,
        default=PackageStatus.PENDING,
        server_default=PackageStatus.PENDING.value,
    )
    weight_g: Mapped[int] = mapped_column(Integer, nullable=False)
    length_mm: Mapped[int | None] = mapped_column(Integer)
    width_mm: Mapped[int | None] = mapped_column(Integer)
    height_mm: Mapped[int | None] = mapped_column(Integer)
    label_url: Mapped[str | None] = mapped_column(String(500))
    label_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    packed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    shipment: Mapped[Shipment] = relationship(back_populates="packages", lazy="raise")


class ShipmentStatusHistory(UUIDPrimaryKeyMixin, Base):
    """The audit trail the brief asks for: one immutable row per transition."""

    __tablename__ = "shipment_status_history"
    __table_args__ = (Index("ix_shipment_status_history_shipment", "shipment_id", "occurred_at"),)

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_role: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=text("now()")
    )


class WarehouseRef(Base):
    """Local, read-only copy of warehouse facts, kept current from events."""

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

    @property
    def is_operational(self) -> bool:
        return self.status == "active" and not self.is_deleted


CONSTRAINT_MESSAGES = {
    "ix_shipments_reference_no": "A shipment with this reference already exists.",
    "uq_shipment_item_sku": "This SKU is already on the shipment.",
    "uq_package_sequence": "A package with this sequence number already exists.",
    "ix_packages_barcode": "This barcode is already in use.",
    "ck_shipment_items_quantity_positive": "Item quantity must be greater than zero.",
}
