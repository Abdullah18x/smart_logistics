"""Revocation reaches every service at once: Identity writes the denylist, and any
service's ``Auth`` (here one built like another service's) refuses the token on
its next request instead of at expiry."""

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.security import HTTPAuthorizationCredentials

from identity import revocation
from identity.api import deps
from identity.config import settings
from sl_platform.auth import Auth, TokenVerifier
from sl_platform.errors import AuthenticationError
from sl_platform.revocation import TokenDenylist
from tests.factories import PASSWORD

LOGIN = "/api/v1/auth/login"


@pytest.fixture
def denylist(monkeypatch) -> TokenDenylist:
    shared = TokenDenylist(FakeAsyncRedis(decode_responses=True), user_ttl_seconds=1800)
    monkeypatch.setattr(revocation, "denylist", shared)
    monkeypatch.setattr(deps.auth, "denylist", shared)
    return shared


@pytest.fixture
def other_service(denylist) -> Auth:
    """Another service's auth: verifies locally, checks the shared denylist."""
    verifier = TokenVerifier(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        public_key_pem=deps.signing_key.public_pem,
    )
    return Auth(verifier, denylist)


async def accepted(auth: Auth, token: str) -> bool:
    try:
        await auth(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    except AuthenticationError:
        return False
    return True


async def sign_in(client, user) -> dict:
    response = await client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_signing_out_everywhere_ends_access_tokens_in_every_service(
    client, factory, other_service
):
    user = await factory.user()
    old = (await sign_in(client, user))["access_token"]
    assert await accepted(other_service, old)
    response = await client.post("/api/v1/auth/logout", json={}, headers=bearer(old))
    assert response.status_code == 200
    assert not await accepted(other_service, old)
    assert (await client.get("/api/v1/auth/me", headers=bearer(old))).status_code == 401
    fresh = (await sign_in(client, user))["access_token"]
    assert await accepted(other_service, fresh)


async def test_signing_out_one_session_ends_only_the_token_that_did_it(
    client, factory, other_service
):
    user = await factory.user()
    first, second = await sign_in(client, user), await sign_in(client, user)
    await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": first["refresh_token"]},
        headers=bearer(first["access_token"]),
    )
    assert not await accepted(other_service, first["access_token"])
    assert await accepted(other_service, second["access_token"])


async def test_a_suspension_takes_effect_on_the_next_request(client, factory, other_service):
    admin = await factory.user(email="admin@example.com")
    user = await factory.user()
    token = (await sign_in(client, user))["access_token"]
    response = await client.patch(
        f"/api/v1/users/{user.id}/status",
        json={"status": "suspended", "reason": "lost device"},
        headers=factory.headers(admin),
    )
    assert response.status_code == 200, response.text
    assert not await accepted(other_service, token)


async def test_without_redis_tokens_still_work_on_signature_alone(client, factory):
    user = await factory.user()
    token = (await sign_in(client, user))["access_token"]
    assert revocation.denylist is None  # tests run without REDIS_URL
    assert (await client.get("/api/v1/auth/me", headers=bearer(token))).status_code == 200
