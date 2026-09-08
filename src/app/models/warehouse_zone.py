"""Functional areas inside a warehouse."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants.enums import ZoneType
from app.models.base import (
    WAREHOUSE_SCHEMA,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.warehouse import Warehouse


class WarehouseZone(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A named area within a warehouse, following the flow of goods.

    Zones give inventory a location more precise than the facility, which is what
    makes picking and staging reportable later.
    """

    __tablename__ = "warehouse_zones"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "code", name="uq_warehouse_zone_code"),
        Index(
            "ix_zones_active_warehouse",
            "warehouse_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("capacity_units > 0", name="capacity_positive"),
        {"schema": WAREHOUSE_SCHEMA},
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WAREHOUSE_SCHEMA}.warehouses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Unique within its warehouse, not globally, e.g. "A1".
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[ZoneType] = mapped_column(
        pg_enum(ZoneType, "zone_type", WAREHOUSE_SCHEMA),
        nullable=False,
        default=ZoneType.STORAGE,
        server_default=ZoneType.STORAGE.value,
    )
    capacity_units: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    warehouse: Mapped["Warehouse"] = relationship(back_populates="zones", lazy="raise")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<WarehouseZone {self.code} {self.type}>"
