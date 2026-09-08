"""User administration over HTTP, and the role gate that guards it."""

import uuid

import pytest

from app.core.enums import UserRole, UserStatus
from tests.factories import TEST_PASSWORD

USERS = "/api/v1/users"
NEW_PASSWORD = "Rotated!Password2026"


def new_user_body(**overrides) -> dict:
    return {
        "email": f"created.{uuid.uuid4().hex[:8]}@transfleet.com",
        "full_name": "Created Person",
        "password": TEST_PASSWORD,
        "role": "customer_support",
    } | overrides


class TestRoleGate:
    @pytest.mark.parametrize(
        "role", [UserRole.CUSTOMER_SUPPORT, UserRole.WAREHOUSE_OPERATOR, UserRole.COURIER]
    )
    async def test_non_admins_cannot_list_users(self, client, factory, role):
        user = await factory.user(role)
        response = await client.get(USERS, headers=factory.auth_headers(user))
        assert response.status_code == 403
        assert response.json()["code"] == "permission_denied"

    async def test_non_admins_cannot_create_users(self, client, factory):
        support = await factory.user(UserRole.CUSTOMER_SUPPORT)
        response = await client.post(
            USERS, json=new_user_body(), headers=factory.auth_headers(support)
        )
        assert response.status_code == 403

    async def test_non_admins_cannot_delete_users(self, client, factory):
        support = await factory.user(UserRole.CUSTOMER_SUPPORT)
        target = await factory.user()
        response = await client.delete(
            f"{USERS}/{target.id}", headers=factory.auth_headers(support)
        )
        assert response.status_code == 403

    async def test_an_anonymous_caller_is_a_401_not_a_403(self, client):
        """Unauthenticated is a different problem from unauthorised."""
        assert (await client.get(USERS)).status_code == 401

    async def test_the_error_names_the_role_required(self, client, factory):
        courier = await factory.user(UserRole.COURIER)
        response = await client.get(USERS, headers=factory.auth_headers(courier))
        assert "admin" in response.json()["message"]


class TestCreate:
    async def test_an_admin_creates_a_user(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        body = new_user_body(role="warehouse_operator")

        response = await client.post(USERS, json=body, headers=factory.auth_headers(admin))
        assert response.status_code == 201
        created = response.json()
        assert created["email"] == body["email"]
        assert created["role"] == "warehouse_operator"
        assert created["status"] == UserStatus.ACTIVE.value

    async def test_the_password_is_not_echoed_back(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        response = await client.post(
            USERS, json=new_user_body(), headers=factory.auth_headers(admin)
        )
        assert "password" not in response.text

    async def test_the_new_account_can_log_in_immediately(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        body = new_user_body()
        await client.post(USERS, json=body, headers=factory.auth_headers(admin))

        login = await client.post(
            "/api/v1/auth/login", json={"email": body["email"], "password": body["password"]}
        )
        assert login.status_code == 200

    async def test_a_duplicate_email_is_a_409(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        body = new_user_body()
        await client.post(USERS, json=body, headers=factory.auth_headers(admin))

        response = await client.post(USERS, json=body, headers=factory.auth_headers(admin))
        assert response.status_code == 409
        assert response.json()["message"] == "A user with this email already exists."

    async def test_a_weak_password_is_a_422_naming_the_field(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        response = await client.post(
            USERS, json=new_user_body(password="weak"), headers=factory.auth_headers(admin)
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["field"] == "password"


class TestListAndFetch:
    async def test_the_listing_is_a_page_envelope(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        response = await client.get(USERS, params={"limit": 5}, headers=factory.auth_headers(admin))
        body = response.json()
        assert set(body) == {"items", "total", "limit", "offset"}
        assert body["limit"] == 5

    async def test_it_filters_by_role(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        await factory.user(UserRole.COURIER, full_name="Filterable Courier")
        response = await client.get(
            USERS,
            params={"role": "courier", "search": "Filterable Courier"},
            headers=factory.auth_headers(admin),
        )
        assert response.json()["total"] == 1

    async def test_it_filters_by_status(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        await factory.user(status=UserStatus.SUSPENDED, full_name="Suspended Soul")
        response = await client.get(
            USERS,
            params={"status": "suspended", "search": "Suspended Soul"},
            headers=factory.auth_headers(admin),
        )
        assert response.json()["total"] == 1

    async def test_an_out_of_range_limit_is_rejected(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        response = await client.get(
            USERS, params={"limit": 500}, headers=factory.auth_headers(admin)
        )
        assert response.status_code == 422

    async def test_fetching_an_unknown_user_is_a_404(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        response = await client.get(f"{USERS}/{uuid.uuid4()}", headers=factory.auth_headers(admin))
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    async def test_a_malformed_id_is_a_422(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        assert (
            await client.get(f"{USERS}/not-a-uuid", headers=factory.auth_headers(admin))
        ).status_code == 422


class TestSelfService:
    @pytest.mark.parametrize("role", list(UserRole))
    async def test_any_role_can_edit_their_own_profile(self, client, factory, role):
        user = await factory.user(role)
        response = await client.patch(
            f"{USERS}/me", json={"full_name": "Renamed Self"}, headers=factory.auth_headers(user)
        )
        assert response.status_code == 200
        assert response.json()["full_name"] == "Renamed Self"

    async def test_a_role_smuggled_into_a_profile_update_is_ignored(self, client, factory):
        """Privilege escalation via a field the schema does not declare."""
        courier = await factory.user(UserRole.COURIER)
        response = await client.patch(
            f"{USERS}/me",
            json={"full_name": "Sneaky", "role": "admin"},
            headers=factory.auth_headers(courier),
        )
        assert response.status_code == 200
        assert response.json()["role"] == UserRole.COURIER.value

    async def test_changing_your_own_password(self, client, factory):
        user = await factory.user()
        response = await client.post(
            f"{USERS}/me/password",
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            headers=factory.auth_headers(user),
        )
        assert response.status_code == 200

        login = await client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": NEW_PASSWORD}
        )
        assert login.status_code == 200

    async def test_the_wrong_current_password_is_a_403(self, client, factory):
        user = await factory.user()
        response = await client.post(
            f"{USERS}/me/password",
            json={"current_password": "WrongPassword!1", "new_password": NEW_PASSWORD},
            headers=factory.auth_headers(user),
        )
        assert response.status_code == 403

    async def test_reusing_the_same_password_is_a_409(self, client, factory):
        user = await factory.user()
        response = await client.post(
            f"{USERS}/me/password",
            json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
            headers=factory.auth_headers(user),
        )
        assert response.status_code == 409

    async def test_a_password_change_signs_other_devices_out(self, client, factory):
        user = await factory.user()
        tokens = (
            await client.post(
                "/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
            )
        ).json()

        await client.post(
            f"{USERS}/me/password",
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            headers=factory.auth_headers(user),
        )
        replay = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replay.status_code == 401


class TestAdministration:
    async def test_an_admin_changes_another_user_s_role(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user(UserRole.COURIER)
        response = await client.patch(
            f"{USERS}/{target.id}/role",
            json={"role": "warehouse_operator", "reason": "Moved to the depot"},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 200
        assert response.json()["role"] == "warehouse_operator"

    async def test_an_admin_cannot_change_their_own_role(self, client, factory):
        """Stops the last admin demoting themselves and locking everyone out."""
        admin = await factory.user(UserRole.ADMIN)
        response = await client.patch(
            f"{USERS}/{admin.id}/role",
            json={"role": "courier"},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 403

    async def test_suspending_an_account_stops_its_tokens_working(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user()
        target_headers = factory.auth_headers(target)

        await client.patch(
            f"{USERS}/{target.id}/status",
            json={"status": "suspended", "reason": "Under review"},
            headers=factory.auth_headers(admin),
        )
        assert (await client.get("/api/v1/auth/me", headers=target_headers)).status_code == 401

    async def test_an_admin_cannot_suspend_themselves(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        response = await client.patch(
            f"{USERS}/{admin.id}/status",
            json={"status": "suspended"},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 403


class TestDeletionIsSoft:
    async def test_deleting_returns_no_content_and_hides_the_user(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user()
        headers = factory.auth_headers(admin)

        assert (await client.delete(f"{USERS}/{target.id}", headers=headers)).status_code == 204
        assert (await client.get(f"{USERS}/{target.id}", headers=headers)).status_code == 404

    async def test_a_deleted_user_can_be_restored(self, client, factory):
        """Only possible because the row was never actually removed."""
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user()
        headers = factory.auth_headers(admin)
        await client.delete(f"{USERS}/{target.id}", headers=headers)

        response = await client.post(f"{USERS}/{target.id}/restore", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == UserStatus.ACTIVE.value
        assert (await client.get(f"{USERS}/{target.id}", headers=headers)).status_code == 200

    async def test_an_admin_cannot_delete_themselves(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        response = await client.delete(f"{USERS}/{admin.id}", headers=factory.auth_headers(admin))
        assert response.status_code == 403
