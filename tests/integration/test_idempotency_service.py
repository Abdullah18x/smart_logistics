"""Replay protection for write requests.

The problem being solved: a client that times out and retries must not create a
second shipment. The unique constraint on ``key`` is both the record and the
concurrency control.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.idempotency_key import IdempotencyKey
from app.services.idempotency_service import (
    IdempotencyConflictError,
    IdempotencyService,
    RequestInProgressError,
)

ENDPOINT = "POST /api/v1/shipments"
BODY = {"origin_warehouse_id": "abc", "items": [{"sku_id": "x", "quantity": 2}]}


@pytest.fixture
def idempotency(db_session) -> IdempotencyService:
    return IdempotencyService(db_session)


@pytest.fixture
def key() -> str:
    return f"test-key-{uuid.uuid4()}"


class TestClaimingAKey:
    async def test_a_first_request_is_told_to_go_ahead(self, idempotency, key):
        assert (
            await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None) is None
        )

    async def test_the_claim_is_recorded(self, idempotency, key, db_session):
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        record = await db_session.scalar(select(IdempotencyKey).where(IdempotencyKey.key == key))
        assert record.endpoint == ENDPOINT
        assert record.is_complete is False
        assert record.expires_at is not None

    async def test_the_caller_is_recorded(self, idempotency, key, db_session, factory):
        user = await factory.user()
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=user.id)
        record = await db_session.scalar(select(IdempotencyKey).where(IdempotencyKey.key == key))
        assert record.user_id == user.id

    async def test_different_keys_do_not_interfere(self, idempotency):
        for _ in range(3):
            fresh = f"key-{uuid.uuid4()}"
            assert (
                await idempotency.begin(key=fresh, endpoint=ENDPOINT, payload=BODY, user_id=None)
                is None
            )


class TestReplay:
    async def test_a_completed_request_replays_its_stored_response(self, idempotency, key):
        """The retry gets the original answer, and nothing happens twice."""
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        await idempotency.complete(key, status_code=201, body={"reference_no": "SL-2026-000042"})

        replay = await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        assert replay == {"reference_no": "SL-2026-000042"}

    async def test_key_order_in_the_body_does_not_defeat_the_replay(self, idempotency, key):
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        await idempotency.complete(key, status_code=201, body={"ok": True})

        reordered = {"items": BODY["items"], "origin_warehouse_id": BODY["origin_warehouse_id"]}
        assert await idempotency.begin(
            key=key, endpoint=ENDPOINT, payload=reordered, user_id=None
        ) == {"ok": True}

    async def test_the_completion_is_timestamped(self, idempotency, key, db_session):
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        await idempotency.complete(key, status_code=201, body={"ok": True})

        record = await db_session.scalar(select(IdempotencyKey).where(IdempotencyKey.key == key))
        assert record.response_status == 201
        assert record.completed_at is not None
        assert record.is_complete is True

    async def test_completing_an_unknown_key_is_harmless(self, idempotency):
        await idempotency.complete("never-claimed", status_code=201, body={})


class TestMisuse:
    async def test_the_same_key_with_a_different_body_is_rejected(self, idempotency, key):
        """Silently replaying the old response would hide a real client bug."""
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        await idempotency.complete(key, status_code=201, body={"ok": True})

        with pytest.raises(IdempotencyConflictError, match="different request body") as exc:
            await idempotency.begin(
                key=key, endpoint=ENDPOINT, payload=BODY | {"changed": True}, user_id=None
            )
        assert exc.value.status_code == 422

    async def test_the_same_key_on_a_different_endpoint_is_rejected(self, idempotency, key):
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        await idempotency.complete(key, status_code=201, body={"ok": True})

        with pytest.raises(IdempotencyConflictError, match="different endpoint"):
            await idempotency.begin(
                key=key, endpoint="POST /api/v1/warehouses", payload=BODY, user_id=None
            )

    async def test_a_request_still_in_flight_is_refused_not_duplicated(self, idempotency, key):
        """The first request has not answered yet; retrying now would double-act."""
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)

        with pytest.raises(RequestInProgressError) as exc:
            await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        assert exc.value.status_code == 409
        assert exc.value.code == "request_in_progress"


class TestDiscard:
    async def test_a_failed_request_frees_its_key(self, idempotency, key):
        """Otherwise a rejected request would block the corrected retry for a day."""
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        await idempotency.discard(key)

        assert (
            await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None) is None
        )

    async def test_the_record_is_removed(self, idempotency, key, db_session):
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        await idempotency.discard(key)
        assert (
            await db_session.scalar(select(IdempotencyKey).where(IdempotencyKey.key == key)) is None
        )

    async def test_a_completed_request_is_never_discarded(self, idempotency, key):
        """Its stored response is the whole point; dropping it would allow a re-run."""
        await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None)
        await idempotency.complete(key, status_code=201, body={"ok": True})
        await idempotency.discard(key)

        assert await idempotency.begin(key=key, endpoint=ENDPOINT, payload=BODY, user_id=None) == {
            "ok": True
        }

    async def test_discarding_an_unknown_key_is_harmless(self, idempotency):
        await idempotency.discard("never-claimed")
