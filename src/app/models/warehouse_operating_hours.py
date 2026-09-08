"""Weekly opening windows for a warehouse."""

import uuid
from datetime import time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    SmallInteger,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import Weekday
from app.models.base import WAREHOUSE_SCHEMA, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.warehouse import Warehouse


class WarehouseOperatingHours(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per weekday.

    Times are local to the warehouse's ``timezone``. Dispatch scheduling uses
    these windows to decide whether a facility can release a shipment now, and
    courier pickup slots are validated against them.
    """

    __tablename__ = "warehouse_operating_hours"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "day_of_week", name="uq_warehouse_operating_day"),
        CheckConstraint("day_of_week BETWEEN 1 AND 7", name="day_of_week_range"),
        # A day is either closed, or has a window that ends after it starts.
        CheckConstraint(
            "(is_closed AND opens_at IS NULL AND closes_at IS NULL) OR "
            "(NOT is_closed AND opens_at IS NOT NULL AND closes_at IS NOT NULL "
            "AND closes_at > opens_at)",
            name="valid_window",
        ),
        {"schema": WAREHOUSE_SCHEMA},
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WAREHOUSE_SCHEMA}.warehouses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_of_week: Mapped[Weekday] = mapped_column(SmallInteger, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time)
    closes_at: Mapped[time | None] = mapped_column(Time)
    is_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    warehouse: Mapped["Warehouse"] = relationship(back_populates="operating_hours", lazy="raise")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        window = "closed" if self.is_closed else f"{self.opens_at}-{self.closes_at}"
        return f"<WarehouseOperatingHours day={self.day_of_week} {window}>"
