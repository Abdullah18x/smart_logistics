"""Replay-safe write requests across any number of pods.

Flow for a request carrying ``Idempotency-Key``:

1. ``claim`` inserts the key in its *own* short transaction. The unique
   (owner, key) constraint lets exactly one pod win; the others get the stored
   response (finished), 409 (still running) or take over a lapsed lease.
2. The endpoint does its work in the request session (flush only).
3. ``record`` writes the response into the claim row *in that same
   transaction*, then the endpoint commits. Work and stored response are
   atomic: there is no window where one exists without the other.
4. On failure the request session rolls back and ``release`` deletes the
   unfinished claim, so the client can correct the request and retry.

``run_idempotent`` packages these steps for an endpoint.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Header
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sl_platform.db import utcnow
from sl_platform.errors import ConflictError, DomainError
from sl_platform.models import IdempotencyKey

IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=255,
        description=(
            "Send a unique value to make retries safe. A repeat with the same key and body "
            "returns the original response instead of acting twice."
        ),
    ),
]


class IdempotencyConflictError(DomainError):
    """The same key was reused with a different request."""

    status_code = 422
    code = "idempotency_key_reuse"


class RequestInProgressError(ConflictError):
    """An identical request is still being processed. Retry shortly."""

    code = "request_in_progress"


def hash_payload(payload: Any) -> str:
    """Stable hash of a request body, insensitive to key order."""
    encoded = json.dumps(
        jsonable_encoder(payload), sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class Claim:
    id: uuid.UUID
    owner: str
    key: str


@dataclass(frozen=True)
class Replay:
    status_code: int
    body: Any


class IdempotencyStore:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        ttl_hours: int = 24,
        lease_seconds: int = 60,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.ttl = timedelta(hours=ttl_hours)
        self.lease = timedelta(seconds=lease_seconds)

    async def claim(self, *, key: str, owner: str, endpoint: str, payload: Any) -> Claim | Replay:
        request_hash = hash_payload(payload)
        now = utcnow()
        async with self.sessionmaker() as session:
            new_id = await session.scalar(
                pg_insert(IdempotencyKey)
                .values(
                    id=uuid.uuid4(),
                    owner=owner,
                    key=key,
                    endpoint=endpoint,
                    request_hash=request_hash,
                    locked_until=now + self.lease,
                    expires_at=now + self.ttl,
                )
                .on_conflict_do_nothing(constraint="uq_idempotency_keys_owner_key")
                .returning(IdempotencyKey.id)
            )
            if new_id is not None:
                await session.commit()
                return Claim(new_id, owner, key)

            # Someone holds this key. Lock the row so concurrent pods evaluate
            # it one at a time (two pods must not both take over a lapsed lease).
            record = await session.scalar(
                select(IdempotencyKey)
                .where(IdempotencyKey.owner == owner, IdempotencyKey.key == key)
                .with_for_update()
            )
            if record is None:  # deleted between our insert and select
                raise RequestInProgressError()

            if record.expires_at <= now:
                # Expired: the key is free to be used afresh.
                record.endpoint = endpoint
                record.request_hash = request_hash
                record.response_status = None
                record.response_body = None
                record.completed_at = None
                record.locked_until = now + self.lease
                record.expires_at = now + self.ttl
                await session.commit()
                return Claim(record.id, owner, key)

            if record.endpoint != endpoint or record.request_hash != request_hash:
                await session.rollback()
                raise IdempotencyConflictError(
                    "This Idempotency-Key was already used with a different request."
                )
            if record.is_complete:
                replay = Replay(record.response_status or 200, record.response_body)
                await session.rollback()
                return replay
            if record.locked_until and record.locked_until > now:
                await session.rollback()
                raise RequestInProgressError()

            # The pod holding the claim died without finishing: take it over.
            record.locked_until = now + self.lease
            await session.commit()
            return Claim(record.id, owner, key)

    async def record(
        self, session: AsyncSession, claim: Claim, *, status_code: int, body: Any
    ) -> None:
        """Store the response inside the caller's (uncommitted) transaction."""
        await session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == claim.id)
            .values(
                response_status=status_code,
                response_body=jsonable_encoder(body),
                completed_at=utcnow(),
                locked_until=None,
            )
        )

    async def release(self, claim: Claim) -> None:
        """Drop an unfinished claim after a failure, in a separate transaction."""
        async with self.sessionmaker() as session:
            await session.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.id == claim.id, IdempotencyKey.completed_at.is_(None)
                )
            )
            await session.commit()

    async def purge_expired(self) -> int:
        async with self.sessionmaker() as session:
            result = await session.execute(
                delete(IdempotencyKey).where(IdempotencyKey.expires_at <= utcnow())
            )
            await session.commit()
            return result.rowcount or 0


async def run_idempotent[ModelT: BaseModel](
    store: IdempotencyStore,
    session: AsyncSession,
    *,
    key: str | None,
    owner: str,
    endpoint: str,
    payload: Any,
    action: Callable[[], Awaitable[ModelT]],
    status_code: int = 200,
) -> ModelT | JSONResponse:
    """Run ``action`` once per (owner, key) and commit its effects.

    ``action`` must only flush; this function commits. Without a key the
    action still runs inside one transaction, just without replay protection.
    """
    if not key:
        result = await action()
        await session.commit()
        return result

    outcome = await store.claim(key=key, owner=owner, endpoint=endpoint, payload=payload)
    if isinstance(outcome, Replay):
        return JSONResponse(
            status_code=outcome.status_code,
            content=outcome.body,
            headers={"Idempotent-Replayed": "true"},
        )
    try:
        result = await action()
        await store.record(session, outcome, status_code=status_code, body=result)
        await session.commit()
    except BaseException:
        await session.rollback()
        await store.release(outcome)
        raise
    return result
