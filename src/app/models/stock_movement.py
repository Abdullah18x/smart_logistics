"""Immutable ledger of every stock change."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.constants.enums import StockMovementType
from app.models.base import (
    CATALOG_SCHEMA,
    IDENTITY_SCHEMA,
    INVENTORY_SCHEMA,
    WAREHOUSE_SCHEMA,
    Base,
    UUIDPrimaryKeyMixin,
    pg_enum,
)


class StockMovement(UUIDPrimaryKeyMixin, Base):
    """One append-only entry per stock change.

    Levels on ``inventory_items`` answer "how much is there now"; this ledger
    answers "how did it get there", which is what makes a stock discrepancy
    investigable. Rows are never updated or deleted, so there is no
    ``updated_at``.
    """

    __tablename__ = "stock_movements"
    __table_args__ = (
        Index("ix_stock_movements_warehouse_occurred", "warehouse_id", "occurred_at"),
        Index("ix_stock_movements_reference", "reference_type", "reference_id"),
        CheckConstraint("quantity_delta <> 0", name="delta_non_zero"),
        {"schema": INVENTORY_SCHEMA},
    )

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{INVENTORY_SCHEMA}.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WAREHOUSE_SCHEMA}.warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CATALOG_SCHEMA}.skus.id", ondelete="RESTRICT"), nullable=False
    )

    type: Mapped[StockMovementType] = mapped_column(
        pg_enum(StockMovementType, "stock_movement_type", INVENTORY_SCHEMA), nullable=False
    )
    #: Signed: positive adds stock, negative removes it.
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Level after this movement, so the ledger can be audited without replay.
    resulting_on_hand: Mapped[int] = mapped_column(Integer, nullable=False)

    # What caused the movement, e.g. ("shipment", <uuid>).
    reference_type: Mapped[str | None] = mapped_column(String(50))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    #: The acting user. Nulls rather than restricts: the ledger entry must
    #: survive even if the account behind it is one day purged.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{IDENTITY_SCHEMA}.users.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(String(500))

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
