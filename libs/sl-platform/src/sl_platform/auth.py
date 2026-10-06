"""Local verification of Identity-signed access tokens.

No service calls Identity on the request path. Each pod fetches Identity's
public key once from ``/.well-known/jwks.json``, keeps it in its own memory,
and checks every token's signature locally. A key id (``kid``) the cache does
not know triggers one re-fetch, which is how key rotation propagates.

The token carries everything authorisation needs: role (the role gate) plus
warehouse ids and courier id (row scoping, ADR-009).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sl_platform.config import ServiceSettings
from sl_platform.errors import AuthenticationError, PermissionDeniedError, UpstreamUnavailableError
from sl_platform.roles import UNRESTRICTED_ROLES, UserRole

ACCESS_TOKEN_TYPE = "access"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, built from token claims alone."""

    user_id: uuid.UUID
    role: UserRole
    warehouse_ids: tuple[uuid.UUID, ...] = ()
    courier_id: uuid.UUID | None = None
    name: str | None = None
    #: The raw bearer token, forwarded when this request calls another service.
    token: str = field(default="", repr=False)

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    @property
    def sees_all_warehouses(self) -> bool:
        return self.role in UNRESTRICTED_ROLES

    def may_touch_warehouse(self, warehouse_id: uuid.UUID) -> bool:
        return self.sees_all_warehouses or warehouse_id in self.warehouse_ids

    @property
    def scoped_warehouse_ids(self) -> list[uuid.UUID] | None:
        """None when unrestricted; otherwise the warehouses this caller may see."""
        if self.sees_all_warehouses:
            return None
        if self.role is UserRole.WAREHOUSE_OPERATOR:
            return list(self.warehouse_ids)
        return []


class TokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key_pem: str | None = None,
        jwks_url: str | None = None,
        cache_seconds: int = 3600,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not public_key_pem and not jwks_url:
            raise ValueError("Configure either JWT_PUBLIC_KEY or JWKS_URL.")
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url
        self.cache_seconds = cache_seconds
        self._transport = transport
        self._static_key = (
            jwt.algorithms.RSAAlgorithm.from_jwk(_pem_to_jwk(public_key_pem))
            if public_key_pem
            else None
        )
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: ServiceSettings) -> TokenVerifier:
        return cls(
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            public_key_pem=settings.jwt_public_key,
            jwks_url=settings.jwks_url,
            cache_seconds=settings.jwks_cache_seconds,
        )

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Token is invalid.") from exc
        key = await self._key_for(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "jti", "role"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Token is invalid.") from exc
        if claims.get("type") != ACCESS_TOKEN_TYPE:
            raise AuthenticationError("Token is not an access token.")
        return claims

    async def _key_for(self, kid: str | None) -> Any:
        if self._static_key is not None:
            return self._static_key
        stale = time.monotonic() - self._fetched_at > self.cache_seconds
        if kid not in self._keys or stale:
            await self._refresh(force=kid not in self._keys)
        if kid not in self._keys:
            raise AuthenticationError("Token was signed with an unknown key.")
        return self._keys[kid]

    async def _refresh(self, *, force: bool) -> None:
        async with self._lock:
            # Another coroutine may have refreshed while we waited for the lock.
            # An unknown kid may re-fetch at most every 10s, so a flood of forged
            # tokens cannot turn into a flood of requests to Identity.
            if time.monotonic() - self._fetched_at < (10 if force else self.cache_seconds):
                return
            try:
                async with httpx.AsyncClient(transport=self._transport, timeout=3.0) as client:
                    response = await client.get(self.jwks_url)
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                if self._keys:
                    return  # keep serving with the keys we already have
                raise UpstreamUnavailableError("Could not load signing keys.") from exc
            self._keys = {
                jwk["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
                for jwk in response.json().get("keys", [])
                if jwk.get("kid")
            }
            self._fetched_at = time.monotonic()

    async def warm_up(self) -> None:
        """Fetch the JWKS at startup so the first request doesn't pay for it."""
        if self._static_key is None:
            await self._refresh(force=True)

    async def try_warm_up(self) -> None:
        """Best-effort warm-up: if Identity is not up yet, the first request
        fetches the key instead of the pod failing to start."""
        try:
            await self.warm_up()
        except Exception:
            return


def _pem_to_jwk(pem: str) -> dict[str, Any]:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    public_key = load_pem_public_key(pem.encode())
    return jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)


_bearer = HTTPBearer(auto_error=False, description="Identity-issued RS256 access token")
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


class Auth:
    """FastAPI dependencies for authentication and the role gate.

    ``Depends(auth)`` resolves the caller; ``Depends(auth.require(...))`` also
    enforces a role. Row scoping is the service's job, using ``Principal``.
    """

    def __init__(self, verifier: TokenVerifier) -> None:
        self.verifier = verifier

    async def __call__(self, credentials: BearerCredentials) -> Principal:
        if credentials is None:
            raise AuthenticationError("Authorization header is missing.")
        claims = await self.verifier.verify(credentials.credentials)
        try:
            return Principal(
                user_id=uuid.UUID(claims["sub"]),
                role=UserRole(claims["role"]),
                warehouse_ids=tuple(uuid.UUID(w) for w in claims.get("warehouse_ids", [])),
                courier_id=uuid.UUID(claims["courier_id"]) if claims.get("courier_id") else None,
                name=claims.get("name"),
                token=credentials.credentials,
            )
        except (ValueError, KeyError) as exc:
            raise AuthenticationError("Token claims are malformed.") from exc

    def require(self, *roles: UserRole) -> Callable[..., Coroutine[Any, Any, Principal]]:
        # A default rather than Annotated[...]: with postponed annotations the
        # closure variable ``self`` would not be resolvable by FastAPI.
        async def dependency(principal: Principal = Depends(self)) -> Principal:  # noqa: B008
            if principal.role not in roles:
                raise PermissionDeniedError(
                    "This action requires one of: " + ", ".join(r.value for r in roles)
                )
            return principal

        return dependency
