"""Notification's tables: the delivery log, suppressions, and the contact details
it keeps from other services' events (it never calls them to look someone up)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from sl_platform.db import TimestampMixin, UUIDPrimaryKeyMixin, new_base, pg_enum, utcnow

Base = new_base()


class Channel(StrEnum):
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"


CHANNEL = pg_enum(Channel, "notification_channel")


class NotificationStatus(StrEnum):
    #: Written, waiting for the send task.
    PENDING = "pending"
    SENT = "sent"
    #: Every attempt failed; ``last_error`` says why. Can be resent.
    FAILED = "failed"
    #: Not sent: the address is on the suppression list.
    SUPPRESSED = "suppressed"


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One message to one recipient on one channel — the delivery log.

    The id is derived from (event, template, channel), so an event delivered
    twice produces the same rows, not two emails. The rendered subject and
    body are stored, so the log shows exactly what was sent.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_shipment", "shipment_id", "created_at"),
        Index("ix_notifications_recipient", "recipient", "created_at"),
        Index(
            "ix_notifications_pending",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    event_type: Mapped[str | None] = mapped_column(String(100))
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    courier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    template: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[Channel] = mapped_column(
        pg_enum(Channel, "notification_channel"), nullable=False
    )
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient_name: Mapped[str | None] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[NotificationStatus] = mapped_column(
        pg_enum(NotificationStatus, "notification_status"),
        nullable=False,
        default=NotificationStatus.PENDING,
        server_default=NotificationStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(1000))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When the next send attempt is due (a retry backs off); the sweep re-queues
    #: pending notifications whose attempt is overdue.
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=text("now()")
    )
    #: For a manual resend: the notification it repeats.
    resend_of: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class Suppression(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An address that must not be messaged on a channel (opt-out, bounced,
    wrong number). Checked at send time, so it also stops queued messages."""

    __tablename__ = "suppressions"
    __table_args__ = (
        UniqueConstraint("channel", "address", name="uq_suppressions_channel_address"),
    )

    channel: Mapped[Channel] = mapped_column(
        pg_enum(Channel, "notification_channel"), nullable=False
    )
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ShipmentRecipient(Base):
    """Who receives a shipment, from ``shipment.created``. Later status events
    carry no contact details; this is where they are looked up."""

    __tablename__ = "shipment_recipients"

    shipment_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    reference_no: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(320))
    destination_city: Mapped[str | None] = mapped_column(String(100))
    origin_warehouse_code: Mapped[str | None] = mapped_column(String(16))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=text("now()")
    )


class CourierContact(Base):
    """A courier's name and contact details, from ``courier.*`` profile events."""

    __tablename__ = "courier_contacts"

    courier_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(320))
    deleted: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


CONSTRAINT_MESSAGES = {
    "uq_suppressions_channel_address": "That address is already suppressed on this channel.",
}
