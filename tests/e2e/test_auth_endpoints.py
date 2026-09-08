"""The authentication endpoints, exercised over HTTP."""

import pytest

from app.core.enums import UserRole, UserStatus
from app.core.tokens import decode_access_token
from tests.factories import TEST_PASSWORD

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
SESSIONS = "/api/v1/auth/sessions"


async def login(client, user, password: str = TEST_PASSWORD):
    return await client.post(LOGIN, json={"email": user.email, "password": password})


class TestLogin:
    async def test_valid_credentials_return_a_token_pair_and_the_profile(self, client, factory):
        """The profile is included so the client avoids an immediate second call."""
        user = await factory.user(UserRole.ADMIN)
        response = await login(client, user)

        assert response.status_code == 200
        body = response.json()
        assert body["access_token"] and body["refresh_token"]
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0
        assert body["user"]["email"] == user.email
        assert body["user"]["role"] == UserRole.ADMIN.value

    async def test_the_password_hash_never_appears_in_the_response(self, client, factory):
        user = await factory.user()
        assert "password" not in (await login(client, user)).text

    async def test_the_role_travels_inside_the_access_token(self, client, factory):
        """So downstream authorisation needs no extra database read."""
        user = await factory.user(UserRole.COURIER)
        token = (await login(client, user)).json()["access_token"]
        assert decode_access_token(token)["role"] == UserRole.COURIER.value

    async def test_a_wrong_password_is_a_401_with_a_stable_code(self, client, factory):
        user = await factory.user()
        response = await login(client, user, password="WrongPassword!1")
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_failed"

    async def test_an_unknown_email_gives_the_identical_response(self, client, factory):
        """Anything else would let an attacker enumerate accounts."""
        user = await factory.user()
        unknown = await client.post(
            LOGIN, json={"email": "nobody@transfleet.com", "password": TEST_PASSWORD}
        )
        wrong = await login(client, user, password="WrongPassword!1")
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()

    async def test_a_failed_login_advertises_the_scheme(self, client, factory):
        user = await factory.user()
        response = await login(client, user, password="WrongPassword!1")
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_a_suspended_account_is_told_it_is_inactive(self, client, factory):
        user = await factory.user(status=UserStatus.SUSPENDED)
        response = await login(client, user)
        assert response.status_code == 401
        assert response.json()["code"] == "account_inactive"

    async def test_a_malformed_email_is_a_validation_error(self, client):
        response = await client.post(LOGIN, json={"email": "nope", "password": "x"})
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
        assert response.json()["detail"][0]["field"] == "email"


class TestMe:
    async def test_a_bearer_token_identifies_the_caller(self, client, factory):
        user = await factory.user(UserRole.CUSTOMER_SUPPORT)
        response = await client.get(ME, headers=factory.auth_headers(user))
        assert response.status_code == 200
        assert response.json()["id"] == str(user.id)

    async def test_no_token_is_a_401(self, client):
        response = await client.get(ME)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_a_forged_token_is_a_401(self, client):
        response = await client.get(ME, headers={"Authorization": "Bearer not.a.token"})
        assert response.status_code == 401

    async def test_a_token_for_a_suspended_account_stops_working(self, client, factory, db_session):
        """Suspension takes effect immediately, not when the token lapses."""
        user = await factory.user()
        headers = factory.auth_headers(user)
        assert (await client.get(ME, headers=headers)).status_code == 200

        user.status = UserStatus.SUSPENDED
        await db_session.commit()
        assert (await client.get(ME, headers=headers)).status_code == 401

    async def test_a_token_for_a_deleted_account_stops_working(self, client, factory, db_session):
        from app.services.user_service import UserService

        admin = await factory.user(UserRole.ADMIN)
        user = await factory.user()
        headers = factory.auth_headers(user)
        await UserService(db_session).delete(user.id, actor=admin)

        assert (await client.get(ME, headers=headers)).status_code == 401


class TestRefresh:
    async def test_a_refresh_returns_a_new_pair(self, client, factory):
        user = await factory.user()
        first = (await login(client, user)).json()

        response = await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
        assert response.status_code == 200
        assert response.json()["refresh_token"] != first["refresh_token"]

    async def test_the_rotated_token_stops_working(self, client, factory):
        user = await factory.user()
        first = (await login(client, user)).json()
        await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})

        replay = await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
        assert replay.status_code == 401

    async def test_replaying_a_rotated_token_ends_every_session(self, client, factory):
        """It has leaked; the safe response is to sign the user out everywhere."""
        user = await factory.user()
        first = (await login(client, user)).json()
        second = (await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})).json()

        await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
        after_theft = await client.post(REFRESH, json={"refresh_token": second["refresh_token"]})
        assert after_theft.status_code == 401

    async def test_an_unknown_token_is_a_401(self, client):
        response = await client.post(REFRESH, json={"refresh_token": "never-issued"})
        assert response.status_code == 401

    async def test_an_empty_token_is_a_validation_error(self, client):
        assert (await client.post(REFRESH, json={"refresh_token": ""})).status_code == 422


class TestLogout:
    async def test_logging_out_revokes_the_presented_session(self, client, factory):
        user = await factory.user()
        tokens = (await login(client, user)).json()

        response = await client.post(
            LOGOUT,
            json={"refresh_token": tokens["refresh_token"]},
            headers=factory.auth_headers(user),
        )
        assert response.status_code == 200
        assert (
            await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
        ).status_code == 401

    async def test_omitting_the_token_signs_out_everywhere(self, client, factory):
        user = await factory.user()
        await login(client, user)
        await login(client, user)

        response = await client.post(LOGOUT, json={}, headers=factory.auth_headers(user))
        assert response.status_code == 200
        assert "2 session(s)" in response.json()["message"]

    async def test_logout_requires_authentication(self, client, factory):
        user = await factory.user()
        tokens = (await login(client, user)).json()
        response = await client.post(LOGOUT, json={"refresh_token": tokens["refresh_token"]})
        assert response.status_code == 401

    async def test_another_user_s_token_cannot_be_revoked(self, client, factory):
        victim = await factory.user()
        attacker = await factory.user()
        tokens = (await login(client, victim)).json()

        response = await client.post(
            LOGOUT,
            json={"refresh_token": tokens["refresh_token"]},
            headers=factory.auth_headers(attacker),
        )
        assert response.status_code == 401


class TestSessions:
    async def test_the_caller_can_see_where_they_are_signed_in(self, client, factory):
        user = await factory.user()
        await client.post(
            LOGIN,
            json={"email": user.email, "password": TEST_PASSWORD},
            headers={"User-Agent": "SmartLogistics-Test/1.0"},
        )

        response = await client.get(SESSIONS, headers=factory.auth_headers(user))
        assert response.status_code == 200
        [session] = response.json()
        assert session["user_agent"] == "SmartLogistics-Test/1.0"

    async def test_the_response_never_includes_the_token_itself(self, client, factory):
        user = await factory.user()
        await login(client, user)
        response = await client.get(SESSIONS, headers=factory.auth_headers(user))
        assert "token_hash" not in response.text
        assert "refresh_token" not in response.text

    async def test_listing_sessions_requires_authentication(self, client):
        assert (await client.get(SESSIONS)).status_code == 401

    @pytest.mark.parametrize("role", list(UserRole))
    async def test_every_role_can_manage_their_own_sessions(self, client, factory, role):
        user = await factory.user(role)
        assert (await client.get(SESSIONS, headers=factory.auth_headers(user))).status_code == 200
