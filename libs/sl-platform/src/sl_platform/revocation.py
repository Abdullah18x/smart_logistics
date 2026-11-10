"""Immediate revocation of access tokens, through Redis.

Access tokens are verified locally and live 15 minutes, so on their own a
suspended user — or one who signed out everywhere — keeps working until the
token expires. Identity closes that gap by writing to a denylist every service
checks on each request:

* ``sl:revoked:user:<user id>`` = the revocation time (ms). Every token of that
  user issued at or before it is refused. Written whenever Identity revokes all
  of a user's sessions: sign-out everywhere, suspension, deactivation, a role or
  warehouse-scope change. It expires after the longest access-token lifetime,
  by which point every token it covers has expired anyway.
* ``sl:revoked:jti:<token id>`` — one token, refused until it expires (a single
  sign-out of the session that holds it).

The check is one ``MGET``. If Redis is unreachable the check *fails open* by
default (requests keep working on signature and expiry alone, a metric and a
log line say so); ``DENYLIST_FAIL_CLOSED=true`` refuses instead.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from prometheus_client import Counter

from sl_platform.config import ServiceSettings

logger = logging.getLogger("sl_platform.revocation")

PREFIX = "sl:revoked"
CHECKS = Counter(
    "token_denylist_checks_total",
    "Access-token denylist checks by outcome: allowed, revoked, unavailable.",
    ["outcome"],
)


def issued_at_ms(claims: dict[str, Any]) -> int:
    """When the token was issued, in ms. ``iat_ms`` is exact; ``iat`` alone is
    rounded down to the second, which is conservative (an older-looking token)."""
    if claims.get("iat_ms") is not None:
        return int(claims["iat_ms"])
    return int(claims.get("iat", 0)) * 1000


class TokenDenylist:
    def __init__(self, redis: Any, *, user_ttl_seconds: int, fail_closed: bool = False) -> None:
        self.redis = redis
        self.user_ttl_seconds = user_ttl_seconds
        self.fail_closed = fail_closed
        self._warned = 0.0

    @classmethod
    def from_settings(cls, settings: ServiceSettings) -> TokenDenylist | None:
        """None when no ``REDIS_URL`` is configured (tests, a minimal setup)."""
        if not settings.redis_url:
            return None
        return cls(
            redis_client(settings.redis_url),
            user_ttl_seconds=settings.denylist_user_ttl_seconds,
            fail_closed=settings.denylist_fail_closed,
        )

    # --- writing (Identity) ------------------------------------------------------------

    async def revoke_user(self, user_id: uuid.UUID, at_ms: int | None = None) -> None:
        at_ms = at_ms or int(time.time() * 1000)
        await self.redis.set(f"{PREFIX}:user:{user_id}", at_ms, ex=self.user_ttl_seconds)

    async def revoke_token(self, token_id: str, expires_at: int) -> None:
        ttl = int(expires_at - time.time()) + 1
        if ttl > 0:
            await self.redis.set(f"{PREFIX}:jti:{token_id}", 1, ex=ttl)

    # --- checking (every service) ------------------------------------------------------

    async def is_revoked(self, claims: dict[str, Any]) -> bool:
        from redis.exceptions import RedisError

        try:
            user_at, token = await self.redis.mget(
                f"{PREFIX}:user:{claims['sub']}", f"{PREFIX}:jti:{claims['jti']}"
            )
        except (RedisError, OSError) as exc:
            CHECKS.labels("unavailable").inc()
            if time.monotonic() - self._warned > 60:
                self._warned = time.monotonic()
                logger.warning(
                    "token denylist unavailable (%s); %s",
                    exc.__class__.__name__,
                    "refusing tokens" if self.fail_closed else "allowing tokens on signature alone",
                )
            return self.fail_closed
        revoked = token is not None or (
            user_at is not None and issued_at_ms(claims) <= int(user_at)
        )
        CHECKS.labels("revoked" if revoked else "allowed").inc()
        return revoked

    async def aclose(self) -> None:
        await self.redis.aclose()


def redis_client(url: str) -> Any:
    """An asyncio Redis client with short timeouts: a slow Redis must not make
    every request slow."""
    from redis.asyncio import Redis

    return Redis.from_url(
        url, socket_timeout=0.25, socket_connect_timeout=0.25, decode_responses=True
    )
