import asyncio
import uuid

from sqlalchemy import select

from identity.db import database
from identity.events import on_warehouse_deleted
from identity.models import RefreshToken, UserWarehouseAssignment
from sl_platform.events import EventEnvelope
from sl_platform.models import OutboxEvent
from sl_platform.roles import UserRole
from tests.factories import PASSWORD

USERS = "/api/v1/users"


def new_user(**overrides):
    return {
        "email": f"{uuid.uuid4().hex[:8]}@transfleet.com",
        "full_name": "New Person",
        "password": "Sufficiently-Strong-1",
        "role": "customer_support",
        **overrides,
    }


async def outbox_types():
    async with database.sessionmaker() as session:
        return list(
            (await session.scalars(select(OutboxEvent.event_type).order_by(OutboxEvent.seq))).all()
        )


class TestCreate:
    async def test_admin_creates_a_user_and_an_event_is_staged(self, client, factory):
        admin = await factory.user()
        response = await client.post(USERS, json=new_user(), headers=factory.headers(admin))
        assert response.status_code == 201
        assert await outbox_types() == ["user.created"]

    async def test_only_admins_may_create(self, client, factory):
        support = await factory.user(UserRole.CUSTOMER_SUPPORT)
        response = await client.post(USERS, json=new_user(), headers=factory.headers(support))
        assert response.status_code == 403

    async def test_duplicate_email_is_409(self, client, factory):
        admin = await factory.user()
        body = new_user()
        await client.post(USERS, json=body, headers=factory.headers(admin))
        again = await client.post(USERS, json=body, headers=factory.headers(admin))
        assert again.status_code == 409

    async def test_weak_password_is_422(self, client, factory):
        admin = await factory.user()
        response = await client.post(
            USERS, json=new_user(password="short"), headers=factory.headers(admin)
        )
        assert response.status_code == 422


class TestIdempotentCreate:
    async def test_a_retry_returns_the_original_response_with_its_status(self, client, factory):
        admin = await factory.user()
        headers = factory.headers(admin) | {"Idempotency-Key": "create-1"}
        body = new_user()
        first = await client.post(USERS, json=body, headers=headers)
        second = await client.post(USERS, json=body, headers=headers)
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert second.headers["Idempotent-Replayed"] == "true"
        assert await outbox_types() == ["user.created"]

    async def test_the_same_key_with_a_different_body_is_422(self, client, factory):
        admin = await factory.user()
        headers = factory.headers(admin) | {"Idempotency-Key": "create-2"}
        await client.post(USERS, json=new_user(), headers=headers)
        response = await client.post(USERS, json=new_user(), headers=headers)
        assert response.json()["code"] == "idempotency_key_reuse"

    async def test_keys_are_scoped_per_caller(self, client, factory):
        one, two = await factory.user(), await factory.user()
        body = new_user()
        first = await client.post(
            USERS, json=body, headers=factory.headers(one) | {"Idempotency-Key": "k"}
        )
        other = await client.post(
            USERS, json=new_user(), headers=factory.headers(two) | {"Idempotency-Key": "k"}
        )
        assert first.status_code == other.status_code == 201
        assert first.json()["id"] != other.json()["id"]

    async def test_a_failed_request_frees_the_key_and_leaves_nothing_behind(self, client, factory):
        admin = await factory.user()
        existing = await factory.user(email="taken@transfleet.com")
        headers = factory.headers(admin) | {"Idempotency-Key": "create-3"}
        failed = await client.post(USERS, json=new_user(email=existing.email), headers=headers)
        assert failed.status_code == 409
        assert await outbox_types() == []  # the failed work was rolled back entirely
        fixed = await client.post(USERS, json=new_user(email=existing.email), headers=headers)
        assert fixed.status_code == 409  # still a conflict — but evaluated, not "in progress"

    async def test_two_concurrent_requests_with_one_key_create_one_user(self, client, factory):
        """Simulates two pods receiving the same retried request at once."""
        admin = await factory.user()
        headers = factory.headers(admin) | {"Idempotency-Key": "race"}
        body = new_user()
        results = await asyncio.gather(
            client.post(USERS, json=body, headers=headers),
            client.post(USERS, json=body, headers=headers),
        )
        statuses = sorted(r.status_code for r in results)
        assert statuses in ([201, 201], [201, 409])
        assert await outbox_types() == ["user.created"]


class TestLifecycle:
    async def test_role_change_revokes_sessions_and_emits_an_event(self, client, factory):
        admin = await factory.user()
        target = await factory.user(UserRole.COURIER)
        await client.post("/api/v1/auth/login", json={"email": target.email, "password": PASSWORD})
        response = await client.patch(
            f"{USERS}/{target.id}/role",
            json={"role": "customer_support"},
            headers=factory.headers(admin),
        )
        assert response.json()["role"] == "customer_support"
        async with database.sessionmaker() as session:
            live = await session.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None)
                )
            )
            assert live.all() == []
        assert "user.role_changed" in await outbox_types()

    async def test_an_admin_cannot_change_their_own_role(self, client, factory):
        admin = await factory.user()
        response = await client.patch(
            f"{USERS}/{admin.id}/role", json={"role": "courier"}, headers=factory.headers(admin)
        )
        assert response.status_code == 403

    async def test_delete_is_soft_and_restorable(self, client, factory):
        admin = await factory.user()
        target = await factory.user(UserRole.COURIER)
        headers = factory.headers(admin)
        assert (await client.delete(f"{USERS}/{target.id}", headers=headers)).status_code == 204
        assert (await client.get(f"{USERS}/{target.id}", headers=headers)).status_code == 404
        restored = await client.post(f"{USERS}/{target.id}/restore", headers=headers)
        assert restored.json()["status"] == "active"


class TestWarehouseScoping:
    async def test_assigning_checks_the_warehouse_service(self, client, factory, warehouses):
        admin = await factory.user()
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        known = uuid.uuid4()
        warehouses.known.add(known)
        ok = await client.post(
            f"{USERS}/{operator.id}/warehouses",
            json={"warehouse_id": str(known)},
            headers=factory.headers(admin),
        )
        unknown = await client.post(
            f"{USERS}/{operator.id}/warehouses",
            json={"warehouse_id": str(uuid.uuid4())},
            headers=factory.headers(admin),
        )
        assert ok.status_code == 201
        assert unknown.status_code == 404
        assert "user.scope_changed" in await outbox_types()

    async def test_only_operators_get_a_warehouse_scope(self, client, factory, warehouses):
        admin = await factory.user()
        courier = await factory.user(UserRole.COURIER)
        response = await client.post(
            f"{USERS}/{courier.id}/warehouses",
            json={"warehouse_id": str(uuid.uuid4())},
            headers=factory.headers(admin),
        )
        assert response.status_code == 409

    async def test_a_deleted_warehouse_event_removes_assignments(self, factory):
        """The event-driven replacement for ON DELETE CASCADE across databases."""
        warehouse_id = uuid.uuid4()
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR, warehouse_ids=(warehouse_id,))
        event = EventEnvelope(
            event_type="warehouse.deleted",
            producer="warehouse",
            key=str(warehouse_id),
            payload={"id": str(warehouse_id)},
        )
        async with database.sessionmaker() as session:
            await on_warehouse_deleted(session, event)
            await session.commit()
        async with database.sessionmaker() as session:
            remaining = await session.scalars(
                select(UserWarehouseAssignment).where(
                    UserWarehouseAssignment.user_id == operator.id
                )
            )
            assert remaining.all() == []
