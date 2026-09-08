"""JWT issuing and validation.

The access token is the only thing standing between a request and the data, so
the negative cases matter more than the happy path.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.constants.enums import TokenType, UserRole
from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.core.tokens import create_access_token, decode_access_token, refresh_token_expiry

USER_ID = uuid.uuid4()


class TestCreateAccessToken:
    def test_returns_a_token_and_its_lifetime(self):
        token, expires_in = create_access_token(USER_ID, UserRole.ADMIN)
        assert token.count(".") == 2
        assert expires_in == settings.access_token_ttl_minutes * 60

    def test_subject_is_the_user_id(self):
        token, _ = create_access_token(USER_ID, UserRole.ADMIN)
        assert decode_access_token(token)["sub"] == str(USER_ID)

    @pytest.mark.parametrize("role", list(UserRole))
    def test_role_travels_in_the_token(self, role):
        """The role is read straight off the token, so it must round-trip exactly."""
        token, _ = create_access_token(USER_ID, role)
        assert decode_access_token(token)["role"] == role.value

    def test_token_is_marked_as_an_access_token(self):
        token, _ = create_access_token(USER_ID, UserRole.COURIER)
        assert decode_access_token(token)["type"] == TokenType.ACCESS.value

    def test_each_token_has_a_unique_id(self):
        """``jti`` is what makes per-token revocation possible later."""
        jtis = {
            decode_access_token(create_access_token(USER_ID, UserRole.ADMIN)[0])["jti"]
            for _ in range(20)
        }
        assert len(jtis) == 20

    def test_expiry_follows_the_configured_ttl(self):
        token, _ = create_access_token(USER_ID, UserRole.ADMIN)
        payload = decode_access_token(token)
        lifetime = payload["exp"] - payload["iat"]
        assert lifetime == settings.access_token_ttl_minutes * 60


class TestDecodeAccessToken:
    def test_rejects_a_tampered_payload(self):
        token, _ = create_access_token(USER_ID, UserRole.COURIER)
        header, payload, signature = token.split(".")
        forged = f"{header}.{payload[:-2]}XX.{signature}"
        with pytest.raises(AuthenticationError, match="invalid"):
            decode_access_token(forged)

    def test_rejects_a_token_signed_with_another_key(self):
        """The classic 'sign your own admin token' attack."""
        forged = jwt.encode(
            {
                "sub": str(USER_ID),
                "role": UserRole.ADMIN.value,
                "type": TokenType.ACCESS.value,
                "jti": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            "an-attackers-key-long-enough-to-avoid-a-length-warning",
            algorithm="HS256",
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(forged)

    def test_rejects_an_expired_token(self):
        expired = jwt.encode(
            {
                "sub": str(USER_ID),
                "role": UserRole.ADMIN.value,
                "type": TokenType.ACCESS.value,
                "jti": str(uuid.uuid4()),
                "iat": datetime.now(UTC) - timedelta(hours=2),
                "exp": datetime.now(UTC) - timedelta(hours=1),
            },
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(AuthenticationError, match="expired"):
            decode_access_token(expired)

    def test_rejects_a_refresh_token_used_as_a_bearer_credential(self):
        """Refresh tokens are revocable; access tokens are not. Never confuse them."""
        refresh = jwt.encode(
            {
                "sub": str(USER_ID),
                "role": UserRole.ADMIN.value,
                "type": TokenType.REFRESH.value,
                "jti": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(days=1),
            },
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(AuthenticationError, match="not an access token"):
            decode_access_token(refresh)

    def test_rejects_a_token_with_no_subject(self):
        no_subject = jwt.encode(
            {
                "role": UserRole.ADMIN.value,
                "type": TokenType.ACCESS.value,
                "jti": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(no_subject)

    def test_rejects_an_unsigned_token(self):
        """``alg: none`` must never be honoured."""
        unsigned = jwt.encode(
            {"sub": str(USER_ID), "jti": str(uuid.uuid4()), "exp": 9999999999},
            key="",
            algorithm="none",
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(unsigned)

    def test_rejects_gibberish(self):
        with pytest.raises(AuthenticationError):
            decode_access_token("not.a.token")

    def test_raises_a_401_domain_error(self):
        with pytest.raises(AuthenticationError) as exc:
            decode_access_token("nonsense")
        assert exc.value.status_code == 401


class TestRefreshTokenExpiry:
    def test_uses_the_configured_window(self):
        expected = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
        assert abs((refresh_token_expiry() - expected).total_seconds()) < 5

    def test_is_timezone_aware(self):
        """A naive datetime would compare wrongly against a timestamptz column."""
        assert refresh_token_expiry().tzinfo is not None

    def test_outlives_the_access_token(self):
        access_lifetime = timedelta(minutes=settings.access_token_ttl_minutes)
        assert refresh_token_expiry() > datetime.now(UTC) + access_lifetime
