import uuid

from sqlalchemy import select

from courier.db import database
from courier.models import Availability, CourierStatus
from sl_platform.models import OutboxEvent
from sl_platform.roles import UserRole
from tests.conftest import as_courier, auth

BASE = "/api/v1/couriers"
NEW = {
    "code": "CR-0101",
    "full_name": "Usman Tariq",
    "phone": "+923001119999",
    "vehicle_type": "motorcycle",
    "max_active_shipments": 8,
    "home_city": "Karachi",
}


async def events(event_type: str) -> list[dict]:
    async with database.scope() as session:
        rows = await session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == event_type)
        )
        return [r.envelope["payload"] for r in rows]


class TestProfiles:
    async def test_admin_creates_a_profile_and_it_is_published(self, client):
        user_id = uuid.uuid4()
        response = await client.post(BASE, headers=auth(), json={**NEW, "user_id": str(user_id)})
        assert response.status_code == 201
        body = response.json()
        assert body["availability"] == "offline"
        assert body["active_assignments"] == 0
        # Identity mirrors the login link from this event.
        assert [e["user_id"] for e in await events("courier.created")] == [str(user_id)]

    async def test_codes_and_user_links_are_unique(self, client):
        user_id = str(uuid.uuid4())
        assert (
            await client.post(BASE, headers=auth(), json={**NEW, "user_id": user_id})
        ).status_code == 201
        again = await client.post(
            BASE, headers=auth(), json={**NEW, "code": "CR-0102", "user_id": user_id}
        )
        assert again.status_code == 409
        dup = await client.post(BASE, headers=auth(), json=NEW)
        assert dup.status_code == 409

    async def test_only_admins_create(self, client):
        for role in (UserRole.CUSTOMER_SUPPORT, UserRole.WAREHOUSE_OPERATOR, UserRole.COURIER):
            assert (await client.post(BASE, headers=auth(role), json=NEW)).status_code == 403

    async def test_list_filters_by_city_and_availability(self, client, fleet):
        await fleet.courier("Karachi")
        await fleet.courier("Karachi", availability=Availability.OFFLINE)
        await fleet.courier("Lahore")
        page = (
            await client.get(
                BASE,
                headers=auth(UserRole.CUSTOMER_SUPPORT),
                params={"city": "karachi", "availability": "available"},
            )
        ).json()
        assert page["total"] == 1

    async def test_a_courier_sees_only_their_own_profile(self, client, fleet):
        me = await fleet.courier()
        other = await fleet.courier()
        assert (await client.get(f"{BASE}/me", headers=as_courier(me.id))).json()["id"] == str(
            me.id
        )
        assert (
            await client.get(f"{BASE}/{other.id}", headers=as_courier(me.id))
        ).status_code == 404
        assert (await client.get(BASE, headers=as_courier(me.id))).status_code == 403


class TestAvailability:
    async def test_a_courier_goes_on_shift_and_it_is_published(self, client, fleet):
        me = await fleet.courier(availability=Availability.OFFLINE)
        response = await client.put(
            f"{BASE}/me/availability", headers=as_courier(me.id), json={"availability": "available"}
        )
        assert response.json()["availability"] == "available"
        assert len(await events("courier.availability_changed")) == 1

    async def test_a_suspended_courier_cannot_go_on_shift(self, client, fleet):
        me = await fleet.courier(availability=Availability.OFFLINE, status=CourierStatus.SUSPENDED)
        response = await client.put(
            f"{BASE}/me/availability", headers=as_courier(me.id), json={"availability": "available"}
        )
        assert response.status_code == 403

    async def test_suspending_takes_the_courier_offline(self, client, fleet):
        courier = await fleet.courier()
        response = await client.patch(
            f"{BASE}/{courier.id}/status", headers=auth(), json={"status": "suspended"}
        )
        assert response.json()["availability"] == "offline"


class TestDeletion:
    async def test_a_courier_with_active_shipments_cannot_be_deleted(self, client, fleet):
        busy = await fleet.courier(load=1)
        response = await client.delete(f"{BASE}/{busy.id}", headers=auth())
        assert response.status_code == 409
        assert response.json()["code"] == "courier_has_active_assignments"

    async def test_delete_and_restore(self, client, fleet):
        idle = await fleet.courier()
        assert (await client.delete(f"{BASE}/{idle.id}", headers=auth())).status_code == 204
        assert (await client.get(f"{BASE}/{idle.id}", headers=auth())).status_code == 404
        restored = await client.post(f"{BASE}/{idle.id}/restore", headers=auth())
        assert restored.status_code == 200
        assert restored.json()["availability"] == "offline"
