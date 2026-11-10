"""Analytics' tables.

Three layers, each rebuildable from the one before:

* ``milestones`` — the fact table: one row per thing that happened to a
  shipment (created, dispatched, delivered, an assignment...), keyed by the
  event id. Append-only.
* ``shipments`` — the latest known state of each shipment (status, warehouse,
  courier, key timestamps), for "how long did delivery take".
* ``daily_rollups`` — counters per day per warehouse / courier, updated in the
  same transaction as the milestone that moves them, and rebuilt nightly from
  ``milestones`` so a bug or a replay can never leave them wrong for long.

Plus ``couriers`` (a replica for utilisation) and ``pipeline_issues`` (dead
letters and failed dispatches: the "failed events / workflow issues" metric).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from sl_platform.db import TimestampMixin, UUIDPrimaryKeyMixin, new_base, pg_enum

Base = new_base()


class Scope(StrEnum):
    WAREHOUSE = "warehouse"
    COURIER = "courier"


SCOPE = pg_enum(Scope, "rollup_scope")


class Metric(StrEnum):
    """Counters kept per day. ``delivery_seconds`` / ``delivered_timed`` give the
    average delivery time without storing every duration twice."""

    CREATED = "created"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    DISPATCH_FAILED = "dispatch_failed"
    ASSIGNED = "assigned"
    DELIVERY_SECONDS = "delivery_seconds"
    DELIVERED_TIMED = "delivered_timed"


class Milestone(Base):
    __tablename__ = "milestones"

    #: The event that recorded it, so a redelivered event cannot count twice.
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    shipment_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    #: A shipment status ('dispatched', 'delivered', ...) or 'assigned'.
    kind: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    courier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_milestones_occurred_at", "occurred_at"),
        Index("ix_milestones_shipment_kind", "shipment_id", "kind"),
    )


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    reference_no: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    #: The shipment's own version: an older event never overwrites a newer state.
    version: Mapped[int] = mapped_column(Integer, default=0)
    service_level: Mapped[str | None] = mapped_column(String(20))
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    warehouse_code: Mapped[str | None] = mapped_column(String(40))
    courier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_shipments_warehouse_status", "warehouse_id", "status"),)


class DailyRollup(Base):
    __tablename__ = "daily_rollups"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[Scope] = mapped_column(SCOPE, primary_key=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    metric: Mapped[str] = mapped_column(String(30), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0)

    __table_args__ = (Index("ix_daily_rollups_scope_day", "scope", "scope_id", "day"),)


class Courier(Base):
    """What utilisation needs: capacity and current load, from courier events."""

    __tablename__ = "couriers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    full_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    home_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    max_active_shipments: Mapped[int] = mapped_column(Integer)
    active_assignments: Mapped[int] = mapped_column(Integer)
    deleted: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IssueKind(StrEnum):
    #: A consumer gave up on an event (``dlq.<consumer>``).
    DEAD_LETTER = "dead_letter"
    #: The dispatch workflow could not dispatch a shipment.
    DISPATCH_FAILED = "dispatch_failed"


ISSUE_KIND = pg_enum(IssueKind, "issue_kind")


class PipelineIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "pipeline_issues"

    kind: Mapped[IssueKind] = mapped_column(ISSUE_KIND)
    #: The consumer that dead-lettered it, or the warehouse a dispatch failed in.
    source: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str | None] = mapped_column(Text)
    event_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str | None] = mapped_column(String(80))
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: 'open', or 'replayed' once the dead letter was sent back.
    status: Mapped[str] = mapped_column(String(20), default="open")
    #: Where it was read from: a dead letter's topic/partition/offset, or the event
    #: id of a failed dispatch. Unique, so reading it twice records it once.
    origin: Mapped[str] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("origin", name="uq_pipeline_issues_origin"),
        Index("ix_pipeline_issues_occurred_at", "occurred_at"),
    )


CONSTRAINT_MESSAGES: dict[str, str] = {}
