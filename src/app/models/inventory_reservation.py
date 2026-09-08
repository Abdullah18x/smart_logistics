"""Stock holds taken by the dispatch workflow."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ReservationStatus
from app.models.base import (
    CATALOG_SCHEMA,
    INVENTORY_SCHEMA,
    SHIPMENT_SCHEMA,
    WAREHOUSE_SCHEMA,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)


class InventoryReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A hold placed on stock for a specific shipment.

    Reservations carry ``expires_at`` so stock can never be stranded: if a
    dispatch workflow dies in a way Temporal cannot compensate, a scheduled
    reaper releases the hold. That failure mode is the one most likely to appear
    under load and the hardest to notice.
    """

    __tablename__ = "inventory_reservations"
    __table_args__ = (
        Index("ix_inventory_reservations_shipment_id", "shipment_id"),
        Index("ix_inventory_reservations_status_expires", "status", "expires_at"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        {"schema": INVENTORY_SCHEMA},
    )

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{INVENTORY_SCHEMA}.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SHIPMENT_SCHEMA}.shipments.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WAREHOUSE_SCHEMA}.warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.skus.id", ondelete="RESTRICT"), nullable=False
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        pg_enum(ReservationStatus, "reservation_status", INVENTORY_SCHEMA),
        nullable=False,
        default=ReservationStatus.HELD,
        server_default=ReservationStatus.HELD.value,
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(255))

    #: Makes ``reserve_inventory`` safe to retry — Temporal retries activities.
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    @property
    def is_active(self) -> bool:
        return self.status is ReservationStatus.HELD
