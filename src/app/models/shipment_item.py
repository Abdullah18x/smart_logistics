"""Line items on a shipment."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    CATALOG_SCHEMA,
    SHIPMENT_SCHEMA,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.models.sku import Sku

if TYPE_CHECKING:
    from app.models.shipment import Shipment


class ShipmentItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One SKU and quantity within a shipment.

    SKU attributes are **snapshotted** at creation. If a product's weight is
    corrected next month, an already-shipped consignment must still show what
    was actually sent — a live join would silently rewrite history.
    """

    __tablename__ = "shipment_items"
    __table_args__ = (
        UniqueConstraint("shipment_id", "sku_id", name="uq_shipment_item_sku"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        {"schema": SHIPMENT_SCHEMA},
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SHIPMENT_SCHEMA}.shipments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Cross-schema foreign key. The snapshot columns below preserve what was
    #: shipped, but the link itself must still resolve.
    sku_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.skus.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Snapshots taken when the line was added.
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False)
    sku_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_weight_g: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_value: Mapped[float | None] = mapped_column(Numeric(12, 2))

    shipment: Mapped["Shipment"] = relationship(back_populates="items", lazy="raise")
    sku: Mapped["Sku"] = relationship(lazy="raise")

    @property
    def total_weight_g(self) -> int:
        return self.quantity * self.unit_weight_g
