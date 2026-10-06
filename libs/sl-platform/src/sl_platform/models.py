"""Platform tables that live in every service's own database.

Import this module from a service's model registry so its migrations create
them. They are infrastructure, owned by no domain:

- ``outbox_events``    events written in the same transaction as the change
- ``processed_events`` event ids a consumer has already handled (dedupe)
- ``idempotency_keys`` stored responses for retried write requests
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sl_platform.db import PlatformBase, utcnow


class OutboxEvent(PlatformBase):
    """An event waiting to be (or already) published to Kafka.

    ``seq`` gives a total order per database, so the relay publishes in the
    order changes were committed.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_unpublished", "seq", postgresql_where=text("published_at IS NULL")),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    #: The full envelope, exactly as it will be published.
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(String(500))


class ProcessedEvent(PlatformBase):
    """One row per (consumer, event) already handled.

    Inserted in the same transaction as the handler's effects: a redelivered
    event — after a rebalance, or a crash before the offset commit — finds its
    row and is skipped, even if another pod is processing it at that moment.
    """

    __tablename__ = "processed_events"

    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class IdempotencyKey(PlatformBase):
    """A claimed ``Idempotency-Key`` and, once finished, the stored response.

    Unique per (owner, key): two users choosing the same key never see each
    other's responses. ``locked_until`` is a lease — a claim whose pod died is
    taken over once it lapses instead of blocking retries forever.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("owner", "key", name="uq_idempotency_keys_owner_key"),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any | None] = mapped_column(JSONB)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None
