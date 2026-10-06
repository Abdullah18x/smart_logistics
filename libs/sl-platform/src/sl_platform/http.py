"""Calling another service.

Rules built in, so no service has to remember them:

- every call has a timeout;
- retries happen only when repeating is safe: reads, or writes carrying an
  ``Idempotency-Key``;
- a circuit breaker stops hammering a service that keeps failing;
- the caller's bearer token, request id and trace context are forwarded;
- a 4xx answer comes back as ``RemoteError`` with the remote status and code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from sl_platform.errors import RemoteError, UpstreamUnavailableError
from sl_platform.logging_setup import request_id_var
from sl_platform.telemetry import current_traceparent

logger = logging.getLogger("sl_platform.http")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RETRYABLE_STATUS = frozenset({502, 503, 504})


class CircuitBreaker:
    """Opens after ``threshold`` consecutive failures; half-opens after ``reset_after``s."""

    def __init__(self, threshold: int = 5, reset_after: float = 30.0) -> None:
        self.threshold = threshold
        self.reset_after = reset_after
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        # Once reset_after has passed the breaker is half-open: one call probes.
        return self.opened_at is not None and time.monotonic() - self.opened_at < self.reset_after

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()


class ServiceClient:
    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        timeout: float = 3.0,
        retries: int = 2,
        breaker: CircuitBreaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.retries = retries
        self.breaker = breaker or CircuitBreaker()
        # One pooled client per target service: keep-alive connections are
        # reused instead of opened per call.
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        idempotency_key: str | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if self.breaker.is_open:
            raise UpstreamUnavailableError(f"{self.name} is unavailable.")

        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if request_id := request_id_var.get():
            headers["X-Request-ID"] = request_id
        if traceparent := current_traceparent():
            headers["traceparent"] = traceparent

        may_retry = method.upper() in SAFE_METHODS or idempotency_key is not None
        attempts = 1 + (self.retries if may_retry else 0)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.request(
                    method, path, headers=headers, json=json, params=params
                )
            except httpx.TransportError as exc:
                last_error = exc
                self.breaker.record_failure()
            else:
                if response.status_code in RETRYABLE_STATUS:
                    last_error = RuntimeError(f"{self.name} answered {response.status_code}")
                    self.breaker.record_failure()
                else:
                    self.breaker.record_success()
                    if response.status_code >= 400:
                        raise _remote_error(self.name, response)
                    return response
            if attempt < attempts:
                await asyncio.sleep(0.1 * 2 ** (attempt - 1))

        logger.warning("%s %s %s failed: %s", self.name, method, path, last_error)
        raise UpstreamUnavailableError(f"{self.name} is unavailable.") from last_error

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()


def _remote_error(service: str, response: httpx.Response) -> RemoteError:
    try:
        body = response.json()
        code = body.get("code", "remote_error")
        message = body.get("message", response.text)
    except ValueError:
        code, message = "remote_error", response.text or f"{service} returned an error."
    # The remote's 401 means *our* forwarded credentials were rejected; surface
    # it as such rather than as a gateway failure.
    return RemoteError(response.status_code, code, message)
