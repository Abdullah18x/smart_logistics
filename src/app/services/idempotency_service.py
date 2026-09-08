"""Replay protection for write requests.

A client that times out and retries must not create a second shipment. The
client sends an ``Idempotency-Key``; the first request records it and stores its
response, and any repeat returns that stored response instead of acting again.

The unique constraint on ``key`` doubles as concurrency control: two
simultaneous requests with the same key race to insert, and the loser is told
the first is still in flight rather than being allowed to proceed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError, DomainError
from app.models.idempotency_key import IdempotencyKey


class IdempotencyConflictError(DomainError):
    """The same key was reused with a different request body."""

    status_code = 422
    code = "idempotency_key_reuse"


class RequestInProgressError(ConflictError):
    """An identical request is still being processed."""

    code = "request_in_progress"


def hash_payload(payload: Any) -> str:
    """Stable hash of a request body, insensitive to key ordering."""
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class IdempotencyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def begin(
        self,
        *,
        key: str,
        endpoint: str,
        payload: Any,
        user_id: uuid.UUID | None,
    ) -> dict | None:
        """Claim the key.

        Returns the stored response when this is a completed replay, or ``None``
        when the caller should go ahead and do the work.
        """
        request_hash = hash_payload(payload)
        existing = await self.session.scalar(
            select(IdempotencyKey).where(IdempotencyKey.key == key)
        )

        if existing is not None:
            return self._evaluate(existing, endpoint, request_hash)

        record = IdempotencyKey(
            key=key,
            endpoint=endpoint,
            request_hash=request_hash,
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(hours=settings.idempotency_ttl_hours),
        )
        self.session.add(record)
        try:
            # Committed immediately so a concurrent duplicate collides here
            # rather than both proceeding to create a shipment.
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            concurrent = await self.session.scalar(
                select(IdempotencyKey).where(IdempotencyKey.key == key)
            )
            if concurrent is None:  # pragma: no cover - defensive
                raise RequestInProgressError() from None
            return self._evaluate(concurrent, endpoint, request_hash)
        return None

    async def complete(self, key: str, *, status_code: int, body: Any) -> None:
        record = await self.session.scalar(select(IdempotencyKey).where(IdempotencyKey.key == key))
        if record is None:  # pragma: no cover - defensive
            return
        record.response_status = status_code
        record.response_body = json.loads(json.dumps(body, default=str))
        record.completed_at = datetime.now(UTC)
        await self.session.commit()

    async def discard(self, key: str) -> None:
        """Drop the claim when the request failed, so the client may retry."""
        record = await self.session.scalar(select(IdempotencyKey).where(IdempotencyKey.key == key))
        if record is not None and not record.is_complete:
            await self.session.delete(record)
            await self.session.commit()

    @staticmethod
    def _evaluate(record: IdempotencyKey, endpoint: str, request_hash: str) -> dict:
        if record.endpoint != endpoint:
            raise IdempotencyConflictError(
                "This Idempotency-Key was already used on a different endpoint."
            )
        if record.request_hash != request_hash:
            # Silently returning the old response would hide a real client bug.
            raise IdempotencyConflictError(
                "This Idempotency-Key was already used with a different request body."
            )
        if not record.is_complete:
            raise RequestInProgressError(
                "An identical request is still being processed. Retry shortly."
            )
        return record.response_body or {}
