"""The shipment: aggregate root of the logistics domain."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ServiceLevel, ShipmentPriority, ShipmentStatus
from app.models.base import (
    COURIER_SCHEMA,
    IDENTITY_SCHEMA,
    SHIPMENT_SCHEMA,
    WAREHOUSE_SCHEMA,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.address import Address
    from app.models.courier import Courier
    from app.models.package import Package
    from app.models.shipment_item import ShipmentItem
    from app.models.warehouse import Warehouse


class Shipment(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A consignment moving from a warehouse to a delivery address.

    Status changes are validated by ``core.shipment_state_machine`` and applied
    with optimistic concurrency on ``version``: two couriers cannot both advance
    the same shipment (ADR-013).
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
        Index("ix_shipments_promised_delivery_at", "promised_delivery_at"),
        CheckConstraint("total_weight_g >= 0", name="weight_non_negative"),
        CheckConstraint("delivery_attempt_count >= 0", name="attempts_non_negative"),
        {"schema": SHIPMENT_SCHEMA},
    )

    #: Human-facing identifier printed on labels, e.g. "SL-2026-000123".
    reference_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    #: The upstream order or ticket this fulfils. Not unique — an order may split.
    customer_reference: Mapped[str | None] = mapped_column(String(100), index=True)

    status: Mapped[ShipmentStatus] = mapped_column(
        pg_enum(ShipmentStatus, "shipment_status", SHIPMENT_SCHEMA),
        nullable=False,
        default=ShipmentStatus.CREATED,
        server_default=ShipmentStatus.CREATED.value,
    )
    service_level: Mapped[ServiceLevel] = mapped_column(
        pg_enum(ServiceLevel, "service_level", SHIPMENT_SCHEMA),
        nullable=False,
        default=ServiceLevel.STANDARD,
        server_default=ServiceLevel.STANDARD.value,
    )
    priority: Mapped[ShipmentPriority] = mapped_column(
        pg_enum(ShipmentPriority, "shipment_priority", SHIPMENT_SCHEMA),
        nullable=False,
        default=ShipmentPriority.NORMAL,
        server_default=ShipmentPriority.NORMAL.value,
    )

    # Cross-schema foreign keys (ADR-008, amended). RESTRICT on the warehouse
    # and courier: neither may be hard-deleted while shipments reference them.
    # SET NULL on the creator: the shipment outlives the account that made it.
    origin_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WAREHOUSE_SCHEMA}.warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    courier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{COURIER_SCHEMA}.couriers.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{IDENTITY_SCHEMA}.users.id", ondelete="SET NULL")
    )

    destination_address_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SHIPMENT_SCHEMA}.addresses.id", ondelete="RESTRICT"), nullable=False
    )

    #: Denormalised from the items, so listings need no join or aggregate.
    total_weight_g: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    declared_value: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="PKR")

    # Lifecycle timestamps. Each is set once, by the transition that earns it.
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

    #: Links the shipment to its Temporal dispatch workflow for diagnosis.
    dispatch_workflow_id: Mapped[str | None] = mapped_column(String(255))

    #: Optimistic-concurrency counter guarding every status transition.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    destination_address: Mapped["Address"] = relationship(lazy="selectin")
    origin_warehouse: Mapped["Warehouse"] = relationship(lazy="raise")
    courier: Mapped["Courier | None"] = relationship(lazy="raise")
    items: Mapped[list["ShipmentItem"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan", lazy="selectin"
    )
    packages: Mapped[list["Package"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Shipment {self.reference_no} {self.status}>"
