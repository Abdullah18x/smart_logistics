"""Stored results of idempotent write requests."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import PLATFORM_SCHEMA, Base, UUIDPrimaryKeyMixin


class IdempotencyKey(UUIDPrimaryKeyMixin, Base):
    """One record per ``Idempotency-Key`` presented by a client.

    Solves the retry problem: a client that times out and resends must not
    create a second shipment. The unique constraint on ``key`` is also the
    concurrency control — two simultaneous requests with the same key race to
    insert, and the loser is told the first is still in flight.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        Index("ix_idempotency_keys_expires_at", "expires_at"),
        {"schema": PLATFORM_SCHEMA},
    )

    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    #: Method and path, so the same key cannot be reused on a different endpoint.
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Hash of the request body. A replay with different content is a client bug.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    #: Null while the original request is still running.
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def is_complete(self) -> bool:
        return self.response_status is not None
