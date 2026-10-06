import uuid
from datetime import timedelta

from sqlalchemy import select

from sl_platform.models import OutboxEvent
from sl_platform.roles import UserRole
from sl_platform.testing import generate_rsa_keypair, mint_token
from tests.conftest import PRIVATE_PEM, auth, operator
from warehouse.db import database

BASE = "/api/v1/warehouses"


def body(code: str | None = None, **overrides) -> dict:
    return {
        "code": code or f"T-{uuid.uuid4().hex[:6]}",
        "name": "Test Facility",
        "address_line1": "Plot 1, Test Road",
        "city": "Karachi",
        "capacity_units": 1000,
        **overrides,
    }


async def create(client, **overrides) -> dict:
    response = await client.post(BASE, json=body(**overrides), headers=auth())
    assert response.status_code == 201, response.text
    return response.json()


async def events() -> list[tuple[str, dict]]:
    async with database.sessionmaker() as session:
        rows = await session.scalars(select(OutboxEvent).order_by(OutboxEvent.seq))
        return [(r.event_type, r.envelope["payload"]) for r in rows]


class TestCreate:
    async def test_admin_creates_and_a_full_snapshot_event_is_staged(self, client):
        created = await create(
            client,
            code="khi-09",
            zones=[{"code": "a1", "name": "Ambient A1", "capacity_units": 100}],
        )
        assert created["code"] == "KHI-09"
        assert created["zones"][0]["code"] == "A1"
        [(event_type, payload)] = await events()
        assert event_type == "warehouse.created"
        assert payload["code"] == "KHI-09" and payload["status"] == "active"

    async def test_duplicate_code_is_409(self, client):
        await create(client, code="DUP-01")
        again = await client.post(BASE, json=body("DUP-01"), headers=auth())
        assert again.status_code == 409

    async def test_operators_cannot_create(self, client):
        response = await client.post(BASE, json=body(), headers=operator())
        assert response.status_code == 403

    async def test_idempotent_retry_creates_one_warehouse(self, client):
        headers = auth() | {"Idempotency-Key": "wh-1"}
        payload = body()
        first = await client.post(BASE, json=payload, headers=headers)
        # Same caller identity is required for a replay, so reuse the same token.
        second = await client.post(BASE, json=payload, headers=headers)
        assert first.json()["id"] == second.json()["id"]
        assert [e for e, _ in await events()] == ["warehouse.created"]


class TestScoping:
    async def test_operators_see_only_their_warehouses(self, client):
        mine = await create(client)
        await create(client)
        listed = await client.get(BASE, headers=operator(uuid.UUID(mine["id"])))
        assert [w["id"] for w in listed.json()["items"]] == [mine["id"]]

    async def test_out_of_scope_is_404_not_403(self, client):
        other = await create(client)
        response = await client.get(f"{BASE}/{other['id']}", headers=operator(uuid.uuid4()))
        assert response.status_code == 404

    async def test_couriers_get_only_the_location_view(self, client):
        await create(client)
        offline = await create(client)
        await client.patch(
            f"{BASE}/{offline['id']}/status", json={"status": "maintenance"}, headers=auth()
        )
        courier = auth(UserRole.COURIER)
        assert (await client.get(BASE, headers=courier)).status_code == 403
        locations = (await client.get(f"{BASE}/locations", headers=courier)).json()
        assert len(locations) == 1
        assert "capacity_units" not in locations[0]


class TestLifecycle:
    async def test_status_change_emits_an_event(self, client):
        created = await create(client)
        response = await client.patch(
            f"{BASE}/{created['id']}/status", json={"status": "maintenance"}, headers=auth()
        )
        assert response.json()["status"] == "maintenance"
        event_type, payload = (await events())[-1]
        assert event_type == "warehouse.status_changed"
        assert payload["previous_status"] == "active" and payload["status"] == "maintenance"

    async def test_delete_is_soft_emits_deleted_and_can_be_restored(self, client):
        created = await create(client)
        assert (await client.delete(f"{BASE}/{created['id']}", headers=auth())).status_code == 204
        assert (await client.get(f"{BASE}/{created['id']}", headers=auth())).status_code == 404
        assert (await events())[-1][0] == "warehouse.deleted"
        restored = await client.post(f"{BASE}/{created['id']}/restore", headers=auth())
        assert restored.json()["status"] == "active"


class TestZonesAndHours:
    async def test_operator_manages_zones_only_in_own_warehouse(self, client):
        mine, other = await create(client), await create(client)
        zone = {"code": "B1", "name": "Bulk B1", "capacity_units": 50}
        ok = await client.post(
            f"{BASE}/{mine['id']}/zones", json=zone, headers=operator(uuid.UUID(mine["id"]))
        )
        denied = await client.post(
            f"{BASE}/{other['id']}/zones", json=zone, headers=operator(uuid.UUID(mine["id"]))
        )
        assert ok.status_code == 201
        assert denied.status_code == 403

    async def test_duplicate_zone_code_is_409(self, client):
        mine = await create(client)
        zone = {"code": "B1", "name": "Bulk B1", "capacity_units": 50}
        await client.post(f"{BASE}/{mine['id']}/zones", json=zone, headers=auth())
        again = await client.post(f"{BASE}/{mine['id']}/zones", json=zone, headers=auth())
        assert again.status_code == 409

    async def test_weekly_schedule_is_replaced_wholesale(self, client):
        mine = await create(client)
        week = {
            "days": [
                {"day_of_week": 1, "opens_at": "08:00:00", "closes_at": "18:00:00"},
                {"day_of_week": 7, "is_closed": True},
            ]
        }
        response = await client.put(
            f"{BASE}/{mine['id']}/operating-hours", json=week, headers=auth()
        )
        assert [d["day_of_week"] for d in response.json()] == [1, 7]

    async def test_closing_before_opening_is_422(self, client):
        mine = await create(client)
        week = {"days": [{"day_of_week": 1, "opens_at": "18:00:00", "closes_at": "08:00:00"}]}
        response = await client.put(
            f"{BASE}/{mine['id']}/operating-hours", json=week, headers=auth()
        )
        assert response.status_code == 422


class TestTokenVerification:
    async def test_expired_token_is_401(self, client):
        token = mint_token(PRIVATE_PEM, ttl=timedelta(seconds=-5))
        response = await client.get(BASE, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    async def test_token_signed_by_another_key_is_401(self, client):
        rogue_private, _ = generate_rsa_keypair()
        token = mint_token(rogue_private)
        response = await client.get(BASE, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    async def test_a_refresh_type_token_is_not_accepted(self, client):
        token = mint_token(PRIVATE_PEM, token_type="refresh")
        response = await client.get(BASE, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
