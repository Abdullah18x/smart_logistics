import asyncio
import uuid

from courier.db import database
from courier.events import on_shipment_status_changed
from courier.models import Availability, CourierStatus
from sl_platform.events import EventEnvelope
from sl_platform.roles import UserRole
from tests.conftest import as_courier, auth, system
from tests.test_couriers import events

ASSIGN = "/api/v1/couriers/assignments"


async def assign(client, warehouse_id, **extra):
    shipment_id = extra.pop("shipment_id", uuid.uuid4())
    return await client.post(
        ASSIGN,
        headers=extra.pop("headers", system()),
        json={"shipment_id": str(shipment_id), "warehouse_id": str(warehouse_id), **extra},
    )


class TestAutomaticAssignment:
    async def test_picks_a_courier_based_at_the_warehouse_first(self, client, fleet):
        wh = await fleet.warehouse("Karachi")
        await fleet.courier("Karachi")  # same city, idle, but not based here
        based = await fleet.courier("Karachi", load=3, home_warehouse_id=wh)
        response = await assign(client, wh)
        assert response.status_code == 201
        body = response.json()
        assert body["courier"]["id"] == str(based.id)
        assert body["assignment"]["method"] == "automatic"
        assert body["courier"]["active_assignments"] == 4

    async def test_then_the_least_loaded_relative_to_capacity(self, client, fleet):
        wh = await fleet.warehouse("Karachi")
        await fleet.courier("Karachi", capacity=4, load=2)  # 50 %
        light = await fleet.courier("Karachi", capacity=10, load=3)  # 30 %
        assert (await assign(client, wh)).json()["courier"]["id"] == str(light.id)

    async def test_skips_couriers_who_cannot_take_it(self, client, fleet):
        wh = await fleet.warehouse("Karachi")
        await fleet.courier("Lahore")
        await fleet.courier("Karachi", availability=Availability.OFFLINE)
        await fleet.courier("Karachi", availability=Availability.ON_BREAK)
        await fleet.courier("Karachi", status=CourierStatus.SUSPENDED)
        await fleet.courier("Karachi", capacity=2, load=2)
        await fleet.courier("Karachi", max_weight_g=1000)
        fit = await fleet.courier("Karachi", max_weight_g=50_000)
        response = await assign(client, wh, total_weight_g=20_000)
        assert response.json()["courier"]["id"] == str(fit.id)

    async def test_nobody_available_is_a_clear_409(self, client, fleet):
        wh = await fleet.warehouse("Peshawar")
        await fleet.courier("Karachi")
        response = await assign(client, wh)
        assert response.status_code == 409
        assert response.json()["code"] == "no_courier_available"

    async def test_repeating_the_call_returns_the_same_assignment(self, client, fleet):
        wh = await fleet.warehouse()
        courier = await fleet.courier()
        shipment_id = uuid.uuid4()
        first = await assign(client, wh, shipment_id=shipment_id)
        again = await assign(client, wh, shipment_id=shipment_id)
        assert first.status_code == 201 and again.status_code == 200
        assert again.json()["assignment"]["id"] == first.json()["assignment"]["id"]
        assert (await fleet.reload(courier.id)).active_assignments == 1
        assert len(await events("courier.assigned")) == 1

    async def test_concurrent_dispatches_never_overbook_the_last_slot(self, client, fleet):
        wh = await fleet.warehouse()
        only = await fleet.courier(capacity=1)
        results = await asyncio.gather(*(assign(client, wh) for _ in range(4)))
        codes = sorted(r.status_code for r in results)
        assert codes == [201, 409, 409, 409]
        assert (await fleet.reload(only.id)).active_assignments == 1

    async def test_unknown_or_closed_warehouse(self, client, fleet):
        closed = await fleet.warehouse(status="maintenance")
        await fleet.courier()
        assert (await assign(client, closed)).status_code == 409
        assert (await assign(client, uuid.uuid4())).status_code == 404


class TestManualAssignment:
    async def test_admin_can_choose_the_courier(self, client, fleet):
        wh = await fleet.warehouse()
        chosen = await fleet.courier()
        response = await assign(client, wh, courier_id=str(chosen.id), headers=auth())
        assert response.status_code == 201
        assert response.json()["assignment"]["method"] == "manual"

    async def test_an_unavailable_courier_is_refused(self, client, fleet):
        wh = await fleet.warehouse()
        offline = await fleet.courier(availability=Availability.OFFLINE)
        response = await assign(client, wh, courier_id=str(offline.id))
        assert response.status_code == 409
        assert response.json()["code"] == "courier_unavailable"

    async def test_a_different_courier_for_an_assigned_shipment_needs_reassign(self, client, fleet):
        wh = await fleet.warehouse()
        await fleet.courier()
        other = await fleet.courier(load=2)
        shipment_id = uuid.uuid4()
        await assign(client, wh, shipment_id=shipment_id)
        response = await assign(client, wh, shipment_id=shipment_id, courier_id=str(other.id))
        assert response.status_code == 409
        assert response.json()["code"] == "already_assigned"

    async def test_only_the_workflow_or_an_admin_assigns(self, client, fleet):
        wh = await fleet.warehouse()
        me = await fleet.courier()
        for headers in (auth(UserRole.CUSTOMER_SUPPORT), as_courier(me.id)):
            assert (await assign(client, wh, headers=headers)).status_code == 403


class TestReassignment:
    async def test_support_hands_the_shipment_to_the_best_other_courier(self, client, fleet):
        wh = await fleet.warehouse()
        first = await fleet.courier(home_warehouse_id=wh)
        second = await fleet.courier(load=1)
        shipment_id = uuid.uuid4()
        await assign(client, wh, shipment_id=shipment_id)
        response = await client.post(
            f"{ASSIGN}/{shipment_id}/reassign",
            headers=auth(UserRole.CUSTOMER_SUPPORT),
            json={"reason": "Vehicle broke down"},
        )
        assert response.status_code == 200
        assert response.json()["courier"]["id"] == str(second.id)
        assert (await fleet.reload(first.id)).active_assignments == 0
        assert (await fleet.reload(second.id)).active_assignments == 2
        history = (
            await client.get(ASSIGN, headers=auth(), params={"shipment_id": str(shipment_id)})
        ).json()["items"]
        assert sorted(a["status"] for a in history) == ["active", "released"]
        assert [e["courier_id"] for e in await events("courier.unassigned")] == [str(first.id)]

    async def test_reassign_to_a_chosen_courier(self, client, fleet):
        wh = await fleet.warehouse()
        await fleet.courier()
        chosen = await fleet.courier(load=3)
        shipment_id = uuid.uuid4()
        await assign(client, wh, shipment_id=shipment_id)
        response = await client.post(
            f"{ASSIGN}/{shipment_id}/reassign",
            headers=auth(),
            json={"courier_id": str(chosen.id), "reason": "Customer asked for a van"},
        )
        assert response.json()["assignment"]["method"] == "manual"

    async def test_reassigning_an_unassigned_shipment_is_404(self, client):
        response = await client.post(
            f"{ASSIGN}/{uuid.uuid4()}/reassign", headers=auth(), json={"reason": "why not"}
        )
        assert response.status_code == 404


class TestEndingAssignments:
    async def test_release_is_a_safe_no_op_when_nothing_is_assigned(self, client):
        response = await client.post(f"{ASSIGN}/{uuid.uuid4()}/release", headers=system(), json={})
        assert response.status_code == 200 and response.json() is None

    async def test_release_frees_capacity(self, client, fleet):
        wh = await fleet.warehouse()
        courier = await fleet.courier()
        shipment_id = uuid.uuid4()
        await assign(client, wh, shipment_id=shipment_id)
        released = await client.post(
            f"{ASSIGN}/{shipment_id}/release", headers=system(), json={"reason": "dispatch failed"}
        )
        assert released.json()["status"] == "released"
        assert (await fleet.reload(courier.id)).active_assignments == 0

    async def test_shipment_events_complete_or_release_the_assignment(self, client, fleet):
        wh = await fleet.warehouse()
        courier = await fleet.courier()
        delivered, cancelled = uuid.uuid4(), uuid.uuid4()
        await assign(client, wh, shipment_id=delivered)
        await assign(client, wh, shipment_id=cancelled)
        for shipment_id, status in ((delivered, "delivered"), (cancelled, "cancelled")):
            async with database.scope() as session:
                await on_shipment_status_changed(
                    session,
                    EventEnvelope(
                        event_type="shipment.status_changed",
                        producer="shipment",
                        key=str(shipment_id),
                        payload={"id": str(shipment_id), "status": status},
                    ),
                )
        rows = (await client.get(ASSIGN, headers=auth())).json()["items"]
        assert sorted(a["status"] for a in rows) == ["completed", "released"]
        assert (await fleet.reload(courier.id)).active_assignments == 0

    async def test_a_courier_lists_their_own_jobs(self, client, fleet):
        wh = await fleet.warehouse()
        me = await fleet.courier()
        await assign(client, wh)
        mine = (await client.get(f"{ASSIGN}/mine", headers=as_courier(me.id))).json()
        assert mine["total"] == 1
