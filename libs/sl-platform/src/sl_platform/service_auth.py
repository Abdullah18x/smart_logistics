"""Service-account tokens: how a service calls others when no user is behind it.

The orchestrator dispatching a shipment acts on its own behalf. It exchanges its
client credentials with Identity for a short-lived `system` token
(``POST /api/v1/auth/service-token``) and attaches it to its calls. Tokens are
cached in memory and renewed shortly before they expire; a 401 forces renewal.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from sl_platform.errors import UpstreamUnavailableError

#: Renew this many seconds before expiry, so a token never expires mid-call.
RENEW_MARGIN_SECONDS = 60


class ServiceTokenProvider:
    def __init__(
        self,
        identity_url: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 3.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self._secret = client_secret
        self._client = httpx.AsyncClient(
            base_url=identity_url, timeout=timeout, transport=transport
        )
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        if self._token and time.monotonic() < self._expires_at - RENEW_MARGIN_SECONDS:
            return self._token
        async with self._lock:  # one renewal at a time, others reuse it
            if self._token and time.monotonic() < self._expires_at - RENEW_MARGIN_SECONDS:
                return self._token
            try:
                response = await self._client.post(
                    "/api/v1/auth/service-token",
                    json={"client_id": self.client_id, "client_secret": self._secret},
                )
            except httpx.TransportError as exc:
                raise UpstreamUnavailableError("identity is unavailable.") from exc
            if response.status_code != 200:
                raise UpstreamUnavailableError(
                    f"identity refused the service token for {self.client_id!r} "
                    f"({response.status_code})."
                )
            body = response.json()
            self._token = body["access_token"]
            self._expires_at = time.monotonic() + int(body["expires_in"])
            return self._token

    def invalidate(self) -> None:
        """Drop the cached token (after a 401), so the next call fetches a new one."""
        self._token = None

    async def aclose(self) -> None:
        await self._client.aclose()
