"""The token denylist and the read-through cache, on an in-memory Redis."""

from __future__ import annotations

import time
import uuid

import pytest
from fakeredis import FakeAsyncRedis
from prometheus_client import REGISTRY
from redis.exceptions import ConnectionError as RedisConnectionError

from sl_platform.cache import Cache
from sl_platform.revocation import TokenDenylist, issued_at_ms


def claims(user: uuid.UUID, issued_ms: int, jti: str | None = None) -> dict:
    return {"sub": str(user), "jti": jti or str(uuid.uuid4()), "iat": issued_ms // 1000,
            "iat_ms": issued_ms}  # fmt: skip


class DownRedis:
    async def mget(self, *_):
        raise RedisConnectionError("refused")

    async def get(self, *_):
        raise RedisConnectionError("refused")

    async def set(self, *_, **__):
        raise RedisConnectionError("refused")


def sample(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class TestDenylist:
    @pytest.fixture
    def denylist(self) -> TokenDenylist:
        return TokenDenylist(FakeAsyncRedis(decode_responses=True), user_ttl_seconds=1800)

    async def test_a_user_revocation_covers_tokens_issued_before_it_only(self, denylist):
        user = uuid.uuid4()
        await denylist.revoke_user(user, at_ms=1_000_500)
        assert await denylist.is_revoked(claims(user, 1_000_400))
        assert await denylist.is_revoked(claims(user, 1_000_500))
        assert not await denylist.is_revoked(claims(user, 1_000_501))
        assert not await denylist.is_revoked(claims(uuid.uuid4(), 1_000_400))

    async def test_a_token_revocation_covers_that_token_until_it_expires(self, denylist):
        user = uuid.uuid4()
        await denylist.revoke_token("t-1", int(time.time()) + 600)
        assert await denylist.is_revoked(claims(user, 1, jti="t-1"))
        assert not await denylist.is_revoked(claims(user, 1, jti="t-2"))
        assert 0 < await denylist.redis.ttl("sl:revoked:jti:t-1") <= 601
        await denylist.revoke_token("old", int(time.time()) - 5)  # already expired: no key
        assert await denylist.redis.exists("sl:revoked:jti:old") == 0

    async def test_user_revocations_expire_after_the_longest_token_lifetime(self, denylist):
        user = uuid.uuid4()
        await denylist.revoke_user(user)
        assert 1790 < await denylist.redis.ttl(f"sl:revoked:user:{user}") <= 1800

    async def test_redis_down_fails_open_by_default_and_closed_when_asked(self):
        before = sample("token_denylist_checks_total", {"outcome": "unavailable"})
        token = claims(uuid.uuid4(), 1)
        assert not await TokenDenylist(DownRedis(), user_ttl_seconds=1).is_revoked(token)
        closed = TokenDenylist(DownRedis(), user_ttl_seconds=1, fail_closed=True)
        assert await closed.is_revoked(token)
        after = sample("token_denylist_checks_total", {"outcome": "unavailable"})
        assert after - before == 2

    def test_whole_second_iat_is_read_conservatively(self):
        assert issued_at_ms({"iat": 12, "iat_ms": 12_345}) == 12_345
        assert issued_at_ms({"iat": 12}) == 12_000


class TestCache:
    async def test_computes_once_then_serves_the_stored_value(self):
        cache, calls = Cache(FakeAsyncRedis(decode_responses=True), "sl:cache:t"), []

        async def compute():
            calls.append(1)
            return {"n": len(calls)}

        first = await cache.get_or_set("k", 30, compute)
        second = await cache.get_or_set("k", 30, compute)
        assert first == second == {"n": 1} and len(calls) == 1

    async def test_a_redis_outage_just_computes(self):
        cache = Cache(DownRedis(), "sl:cache:down")

        async def compute():
            return 42

        assert await cache.get_or_set("k", 30, compute) == 42
        assert sample("cache_requests_total", {"namespace": "sl:cache:down", "outcome": "error"})
