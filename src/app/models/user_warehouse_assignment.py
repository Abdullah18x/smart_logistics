"""Warehouse scoping for warehouse operators."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import IDENTITY_SCHEMA, WAREHOUSE_SCHEMA, Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class UserWarehouseAssignment(UUIDPrimaryKeyMixin, Base):
    """Scopes a warehouse operator to the warehouses they may act on.

    Backs the row-level scoping rule in ADR-009: the role grants the endpoint,
    this grants the rows.
    """

    __tablename__ = "user_warehouse_assignments"
    __table_args__ = (
        Index("uq_user_warehouse_assignment", "user_id", "warehouse_id", unique=True),
        {"schema": IDENTITY_SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{IDENTITY_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    # Cross-schema foreign key (ADR-008, amended). Removing an operator's
    # warehouse assignment when the warehouse goes is the correct behaviour.
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WAREHOUSE_SCHEMA}.warehouses.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="warehouse_assignments", lazy="raise")
