import asyncio
import uuid

import httpx
import jwt

from identity.main import app
from identity.models import UserStatus
from sl_platform.auth import TokenVerifier
from sl_platform.roles import UserRole
from tests.factories import PASSWORD

LOGIN = "/api/v1/auth/login"


async def login(client, email, password=PASSWORD):
    return await client.post(LOGIN, json={"email": email, "password": password})


class TestLogin:
    async def test_returns_a_token_pair_and_profile(self, client, factory):
        user = await factory.user(UserRole.CUSTOMER_SUPPORT)
        response = await login(client, user.email)
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["id"] == str(user.id)
        assert body["expires_in"] == 15 * 60

    async def test_wrong_password_is_401_without_revealing_which_part_failed(self, client, factory):
        user = await factory.user()
        wrong = await login(client, user.email, "nope")
        unknown = await login(client, "nobody@transfleet.com")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["message"] == unknown.json()["message"]

    async def test_five_failures_lock_the_account(self, client, factory):
        user = await factory.user()
        for _ in range(5):
            await login(client, user.email, "wrong-password")
        response = await login(client, user.email)  # correct password, still locked
        assert response.status_code == 401
        assert response.json()["code"] == "account_locked"

    async def test_suspended_accounts_cannot_log_in(self, client, factory):
        user = await factory.user(status=UserStatus.SUSPENDED)
        response = await login(client, user.email)
        assert response.json()["code"] == "account_inactive"


class TestTokenClaims:
    async def test_an_operator_token_carries_their_warehouse_scope(self, client, factory):
        warehouses = (uuid.uuid4(), uuid.uuid4())
        user = await factory.user(UserRole.WAREHOUSE_OPERATOR, warehouse_ids=warehouses)
        token = (await login(client, user.email)).json()["access_token"]
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["role"] == "warehouse_operator"
        assert sorted(claims["warehouse_ids"]) == sorted(str(w) for w in warehouses)
        assert claims["type"] == "access"
        assert claims["iss"] == "smartlogistics-identity"

    async def test_another_service_verifies_the_token_through_the_jwks_endpoint(
        self, client, factory
    ):
        """What every other service does: fetch JWKS once, verify locally."""
        user = await factory.user()
        token = (await login(client, user.email)).json()["access_token"]
        verifier = TokenVerifier(
            issuer="smartlogistics-identity",
            audience="smartlogistics",
            jwks_url="http://identity/.well-known/jwks.json",
            transport=httpx.ASGITransport(app=app),
        )
        claims = await verifier.verify(token)
        assert claims["sub"] == str(user.id)

    async def test_jwks_exposes_only_the_public_key(self, client):
        keys = (await client.get("/.well-known/jwks.json")).json()["keys"]
        assert keys[0]["kid"] == "test-key"
        assert "d" not in keys[0]  # the private exponent never leaves the service


class TestRefresh:
    async def test_rotates_the_refresh_token(self, client, factory):
        user = await factory.user()
        first = (await login(client, user.email)).json()["refresh_token"]
        rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
        assert rotated.status_code == 200
        assert rotated.json()["refresh_token"] != first

    async def test_replaying_a_rotated_token_revokes_every_session(self, client, factory):
        user = await factory.user()
        first = (await login(client, user.email)).json()["refresh_token"]
        second = (await client.post("/api/v1/auth/refresh", json={"refresh_token": first})).json()
        replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
        assert replay.json()["code"] == "refresh_token_reused"
        after = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
        )
        assert after.status_code == 401

    async def test_two_concurrent_refreshes_cannot_both_succeed(self, client, factory):
        """Two pods racing on one token: the row lock lets exactly one rotate it."""
        user = await factory.user()
        token = (await login(client, user.email)).json()["refresh_token"]
        results = await asyncio.gather(
            client.post("/api/v1/auth/refresh", json={"refresh_token": token}),
            client.post("/api/v1/auth/refresh", json={"refresh_token": token}),
        )
        assert sorted(r.status_code for r in results) == [200, 401]


class TestMe:
    async def test_returns_the_profile(self, client, factory):
        user = await factory.user()
        response = await client.get("/api/v1/auth/me", headers=factory.headers(user))
        assert response.json()["email"] == user.email

    async def test_a_user_suspended_after_login_is_refused_here_immediately(self, client, factory):
        user = await factory.user(status=UserStatus.SUSPENDED)
        response = await client.get("/api/v1/auth/me", headers=factory.headers(user))
        assert response.status_code == 401

    async def test_missing_token_is_401(self, client):
        assert (await client.get("/api/v1/auth/me")).status_code == 401
