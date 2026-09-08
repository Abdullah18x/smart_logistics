"""Physical parcels making up a shipment."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants.enums import PackageStatus
from app.models.base import SHIPMENT_SCHEMA, Base, TimestampMixin, UUIDPrimaryKeyMixin, pg_enum

if TYPE_CHECKING:
    from app.models.shipment import Shipment


class Package(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A parcel. One shipment may be split across several.

    The barcode is what a warehouse scanner and a courier's device actually
    read, which is why it is unique across the whole system rather than per
    shipment.
    """

    __tablename__ = "packages"
    __table_args__ = (
        UniqueConstraint("shipment_id", "sequence_no", name="uq_package_sequence"),
        CheckConstraint("weight_g > 0", name="weight_positive"),
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
        {"schema": SHIPMENT_SCHEMA},
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SHIPMENT_SCHEMA}.shipments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Position within the shipment: "parcel 2 of 3".
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    barcode: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    status: Mapped[PackageStatus] = mapped_column(
        pg_enum(PackageStatus, "package_status", SHIPMENT_SCHEMA),
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

    shipment: Mapped["Shipment"] = relationship(back_populates="packages", lazy="raise")
