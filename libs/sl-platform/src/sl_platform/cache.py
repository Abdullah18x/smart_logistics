"""A small read-through cache in Redis for responses that are costly to compute
and fine to serve a few seconds stale (dashboards, aggregates).

Best effort in both directions: if Redis is down or slow, ``get_or_set`` simply
computes the value — a cache outage never fails a request.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from prometheus_client import Counter

from sl_platform.config import ServiceSettings
from sl_platform.revocation import redis_client

logger = logging.getLogger("sl_platform.cache")

REQUESTS = Counter(
    "cache_requests_total", "Cache lookups by outcome: hit, miss, error.", ["namespace", "outcome"]
)


class Cache:
    def __init__(self, redis: Any, namespace: str) -> None:
        self.redis = redis
        self.namespace = namespace

    @classmethod
    def from_settings(cls, settings: ServiceSettings) -> Cache | None:
        if not settings.redis_url:
            return None
        return cls(redis_client(settings.redis_url), f"sl:cache:{settings.service_name}")

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    async def get_or_set[T](
        self,
        key: str,
        ttl_seconds: int,
        compute: Callable[[], Awaitable[T]],
        *,
        dump: Callable[[T], Any] = lambda v: v,
        load: Callable[[Any], T] = lambda v: v,
    ) -> T:
        from redis.exceptions import RedisError

        try:
            raw = await self.redis.get(self._key(key))
        except (RedisError, OSError):
            REQUESTS.labels(self.namespace, "error").inc()
            return await compute()
        if raw is not None:
            REQUESTS.labels(self.namespace, "hit").inc()
            return load(json.loads(raw))
        REQUESTS.labels(self.namespace, "miss").inc()
        value = await compute()
        try:
            await self.redis.set(
                self._key(key), json.dumps(dump(value), default=str), ex=ttl_seconds
            )
        except (RedisError, OSError):
            REQUESTS.labels(self.namespace, "error").inc()
        return value
