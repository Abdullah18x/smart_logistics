"""The record of a shipment being offered to, and handled by, a courier."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants.enums import AssignmentStatus
from app.models.base import (
    COURIER_SCHEMA,
    IDENTITY_SCHEMA,
    SHIPMENT_SCHEMA,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.courier import Courier
    from app.models.shipment import Shipment


class CourierAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One offer of one shipment to one courier.

    A shipment accumulates several of these over its life — offered, rejected,
    reassigned, finally completed. ``shipments.courier_id`` records only who
    holds it *now*; this table is what makes reassignment history, response
    times and Courier Utilization Rate reportable.

    It is also what the dispatch saga compensates: ``unassign_courier`` marks
    the row ``reassigned`` rather than silently nulling a column.
    """

    __tablename__ = "courier_assignments"
    __table_args__ = (
        Index("ix_courier_assignments_shipment_id", "shipment_id"),
        Index("ix_courier_assignments_courier_status", "courier_id", "status"),
        # Supports "who is the current assignee for this shipment".
        Index("ix_courier_assignments_shipment_status", "shipment_id", "status"),
        {"schema": COURIER_SCHEMA},
    )

    courier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{COURIER_SCHEMA}.couriers.id", ondelete="RESTRICT"), nullable=False
    )
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SHIPMENT_SCHEMA}.shipments.id", ondelete="RESTRICT"), nullable=False
    )

    status: Mapped[AssignmentStatus] = mapped_column(
        pg_enum(AssignmentStatus, "assignment_status", COURIER_SCHEMA),
        nullable=False,
        default=AssignmentStatus.OFFERED,
        server_default=AssignmentStatus.OFFERED.value,
    )

    #: Which attempt this is for the shipment — 1 is the first offer.
    sequence_no: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Why it was rejected or reassigned. Feeds the delay-insight engine in Part B.
    reason: Mapped[str | None] = mapped_column(String(500))
    #: Ops user who assigned, or null when the dispatch workflow chose.
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{IDENTITY_SCHEMA}.users.id", ondelete="SET NULL")
    )

    courier: Mapped["Courier"] = relationship(back_populates="assignments", lazy="raise")
    shipment: Mapped["Shipment"] = relationship(lazy="raise")

    @property
    def is_current(self) -> bool:
        return self.status in (AssignmentStatus.OFFERED, AssignmentStatus.ACCEPTED)

    @property
    def response_seconds(self) -> float | None:
        """How long the courier took to accept or reject. A dispatch KPI."""
        if self.responded_at is None:
            return None
        return (self.responded_at - self.assigned_at).total_seconds()
