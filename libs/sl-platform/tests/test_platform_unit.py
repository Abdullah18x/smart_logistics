"""Unit tests for the shared library. No database or broker needed."""

import uuid

import httpx
import jwt
import pytest

from sl_platform.auth import Principal, TokenVerifier
from sl_platform.errors import AuthenticationError, RemoteError, UpstreamUnavailableError
from sl_platform.http import CircuitBreaker, ServiceClient
from sl_platform.idempotency import hash_payload
from sl_platform.roles import UserRole
from sl_platform.testing import TEST_KID, generate_rsa_keypair, mint_token


class TestHashPayload:
    def test_is_insensitive_to_key_order(self):
        assert hash_payload({"a": 1, "b": [1, 2]}) == hash_payload({"b": [1, 2], "a": 1})

    def test_differs_for_different_bodies(self):
        assert hash_payload({"a": 1}) != hash_payload({"a": 2})


class TestPrincipal:
    def test_operator_scope_is_the_token_warehouses(self):
        w = uuid.uuid4()
        p = Principal(user_id=uuid.uuid4(), role=UserRole.WAREHOUSE_OPERATOR, warehouse_ids=(w,))
        assert p.scoped_warehouse_ids == [w]
        assert p.may_touch_warehouse(w) and not p.may_touch_warehouse(uuid.uuid4())

    def test_admin_and_support_are_unrestricted(self):
        for role in (UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT):
            assert Principal(user_id=uuid.uuid4(), role=role).scoped_warehouse_ids is None

    def test_courier_sees_no_warehouse_rows(self):
        assert Principal(user_id=uuid.uuid4(), role=UserRole.COURIER).scoped_warehouse_ids == []


class TestCircuitBreaker:
    def test_opens_after_threshold_and_resets_on_success(self):
        breaker = CircuitBreaker(threshold=2, reset_after=60)
        breaker.record_failure()
        assert not breaker.is_open
        breaker.record_failure()
        assert breaker.is_open
        breaker.record_success()
        assert not breaker.is_open


def client_with(handler) -> tuple[ServiceClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    return ServiceClient(
        "svc", "http://svc", retries=2, transport=httpx.MockTransport(record)
    ), seen


class TestServiceClient:
    async def test_forwards_token_and_idempotency_key(self):
        client, seen = client_with(lambda r, n: httpx.Response(200, json={}))
        await client.post("/x", token="abc", idempotency_key="k1", json={})
        assert seen[0].headers["Authorization"] == "Bearer abc"
        assert seen[0].headers["Idempotency-Key"] == "k1"

    async def test_a_4xx_comes_back_with_the_remote_code(self):
        client, _ = client_with(
            lambda r, n: httpx.Response(409, json={"code": "insufficient_stock", "message": "no"})
        )
        with pytest.raises(RemoteError) as raised:
            await client.post("/x", json={})
        assert (raised.value.status_code, raised.value.code) == (409, "insufficient_stock")

    async def test_unsafe_writes_without_a_key_are_not_retried(self):
        client, seen = client_with(lambda r, n: httpx.Response(503))
        with pytest.raises(UpstreamUnavailableError):
            await client.post("/x", json={})
        assert len(seen) == 1

    async def test_keyed_writes_are_retried_until_they_succeed(self):
        client, seen = client_with(
            lambda r, n: httpx.Response(503) if n < 3 else httpx.Response(200, json={})
        )
        await client.post("/x", idempotency_key="k", json={})
        assert len(seen) == 3


class TestTokenVerifier:
    async def test_fetches_jwks_once_and_refetches_for_a_new_kid(self):
        old_private, old_public = generate_rsa_keypair()
        new_private, new_public = generate_rsa_keypair()
        keys = {"current": [(TEST_KID, old_public)]}
        fetches = []

        def jwks(request):
            fetches.append(1)
            out = []
            for kid, pem in keys["current"]:
                from cryptography.hazmat.primitives.serialization import load_pem_public_key

                jwk = jwt.algorithms.RSAAlgorithm.to_jwk(
                    load_pem_public_key(pem.encode()), as_dict=True
                )
                out.append(jwk | {"kid": kid})
            return httpx.Response(200, json={"keys": out})

        verifier = TokenVerifier(
            issuer="smartlogistics-identity",
            audience="smartlogistics",
            jwks_url="http://identity/.well-known/jwks.json",
            transport=httpx.MockTransport(jwks),
        )
        await verifier.verify(mint_token(old_private))
        await verifier.verify(mint_token(old_private))
        assert len(fetches) == 1  # cached in this pod's memory

        # Identity rotates: the new token carries a kid the cache does not know.
        keys["current"].append(("rotated", new_public))
        token = jwt.encode(
            jwt.decode(mint_token(new_private), options={"verify_signature": False}),
            new_private,
            algorithm="RS256",
            headers={"kid": "rotated"},
        )
        verifier._fetched_at -= 60  # past the anti-flood window
        await verifier.verify(token)
        assert len(fetches) == 2

    async def test_rejects_wrong_audience(self):
        private, public = generate_rsa_keypair()
        verifier = TokenVerifier(
            issuer="smartlogistics-identity", audience="smartlogistics", public_key_pem=public
        )
        with pytest.raises(AuthenticationError):
            await verifier.verify(mint_token(private, audience="someone-else"))
