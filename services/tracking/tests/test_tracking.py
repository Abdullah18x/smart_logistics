import uuid

from sl_platform.roles import UserRole
from tests.conftest import as_courier, at, auth, open_journey, system
from tracking.api.deps import journeys, store

BASE = "/api/v1/tracking"


def milestone(shipment_id, name, minutes, **extra) -> dict:
    return {
        "event_id": str(extra.pop("event_id", uuid.uuid4())),
        "shipment_id": shipment_id,
        "type": "milestone",
        "milestone": name,
        "occurred_at": at(minutes),
        **extra,
    }


async def report(client, courier_id, update):
    return await client.post(f"{BASE}/updates", json=update, headers=as_courier(courier_id))


async def journey(client, shipment_id) -> dict:
    return (await client.get(f"{BASE}/journeys/{shipment_id}", headers=auth())).json()


class TestJourneys:
    async def test_opening_is_idempotent_and_starts_at_dispatched(self, client):
        opened = await open_journey(client)
        assert opened["milestone"] == "dispatched" and opened["status"] == "active"
        again = await client.post(
            f"{BASE}/journeys",
            json={
                "shipment_id": opened["shipment_id"],
                "reference_no": opened["reference_no"],
                "courier_id": opened["courier_id"],
                "origin_warehouse_id": opened["origin_warehouse_id"],
                "destination_city": "Karachi",
            },
            headers=system(),
        )
        assert again.status_code == 200
        timeline = (await journey(client, opened["shipment_id"]))["events"]
        assert [e["milestone"] for e in timeline] == ["dispatched"]

    async def test_only_the_workflow_opens_journeys(self, client):
        response = await client.post(
            f"{BASE}/journeys",
            json={
                "shipment_id": str(uuid.uuid4()),
                "reference_no": "SL-X",
                "courier_id": str(uuid.uuid4()),
                "origin_warehouse_id": str(uuid.uuid4()),
                "destination_city": "Karachi",
            },
            headers=auth(UserRole.CUSTOMER_SUPPORT),
        )
        assert response.status_code == 403

    async def test_cancel_then_reopen_for_a_retried_dispatch(self, client):
        opened = await open_journey(client)
        sid = opened["shipment_id"]
        cancelled = await client.post(
            f"{BASE}/journeys/{sid}/cancel", json={"reason": "dispatch failed"}, headers=system()
        )
        assert cancelled.json()["status"] == "cancelled"
        new_courier = str(uuid.uuid4())
        reopened = await client.post(
            f"{BASE}/journeys",
            json={**{k: opened[k] for k in ("reference_no", "origin_warehouse_id")},
                  "shipment_id": sid, "courier_id": new_courier, "destination_city": "Karachi"},
            headers=system(),
        )  # fmt: skip
        assert reopened.json()["status"] == "active"
        assert reopened.json()["courier_id"] == new_courier

    async def test_cancelling_an_unknown_journey_is_404(self, client):
        response = await client.post(
            f"{BASE}/journeys/{uuid.uuid4()}/cancel", json={}, headers=system()
        )
        assert response.status_code == 404


class TestFieldUpdates:
    async def test_a_milestone_is_recorded_once_however_often_it_is_sent(self, client):
        courier = uuid.uuid4()
        sid = (await open_journey(client, courier_id=courier))["shipment_id"]
        update = milestone(sid, "picked_up", 5)
        first = await report(client, courier, update)
        again = await report(client, courier, update)
        assert first.status_code == 202 and first.json()["status"] == "recorded"
        assert again.json()["status"] == "duplicate"
        state = await journey(client, sid)
        assert state["milestone"] == "picked_up"
        assert [e["milestone"] for e in state["events"]] == ["dispatched", "picked_up"]

    async def test_a_late_update_joins_the_timeline_without_moving_the_journey_back(self, client):
        courier = uuid.uuid4()
        sid = (await open_journey(client, courier_id=courier))["shipment_id"]
        await report(client, courier, milestone(sid, "out_for_delivery", 30))
        await report(client, courier, milestone(sid, "picked_up", 5))  # arrives late
        state = await journey(client, sid)
        assert state["milestone"] == "out_for_delivery"
        timeline = [e["milestone"] for e in state["events"]]
        assert timeline.index("picked_up") < timeline.index("out_for_delivery")

    async def test_failed_attempts_are_counted_once_each(self, client):
        courier = uuid.uuid4()
        sid = (await open_journey(client, courier_id=courier))["shipment_id"]
        failed = milestone(sid, "delivery_failed", 40, failure_reason="Nobody home")
        await report(client, courier, failed)
        await report(client, courier, failed)  # retried upload
        await report(
            client, courier, milestone(sid, "delivery_failed", 90, failure_reason="Closed")
        )
        assert (await journey(client, sid))["delivery_attempts"] == 2

    async def test_delivery_completes_the_journey_and_closes_it(self, client):
        courier = uuid.uuid4()
        sid = (await open_journey(client, courier_id=courier))["shipment_id"]
        await report(client, courier, milestone(sid, "delivered", 60))
        state = await journey(client, sid)
        assert state["status"] == "completed" and state["completed_at"]
        late = await report(client, courier, milestone(sid, "in_transit", 70))
        assert late.json()["status"] == "rejected" and late.json()["code"] == "journey_closed"

    async def test_only_the_assigned_courier_reports(self, client):
        sid = (await open_journey(client))["shipment_id"]
        response = await report(client, uuid.uuid4(), milestone(sid, "picked_up", 5))
        assert response.json()["code"] == "permission_denied"

    async def test_field_rules(self, client):
        courier = uuid.uuid4()
        sid = (await open_journey(client, courier_id=courier))["shipment_id"]
        no_reason = milestone(sid, "delivery_failed", 5)
        assert (await report(client, courier, no_reason)).status_code == 422
        returned = await report(client, courier, milestone(sid, "returned", 5))
        assert returned.json()["code"] == "invalid_update"
        future = milestone(sid, "picked_up", 0)
        future["occurred_at"] = "2099-01-01T00:00:00+00:00"
        assert (await report(client, courier, future)).json()["code"] == "invalid_update"

    async def test_location_keeps_the_newest_fix(self, client):
        courier = uuid.uuid4()
        sid = (await open_journey(client, courier_id=courier))["shipment_id"]
        for minutes, lat in ((20, 24.9), (10, 24.1)):
            await report(
                client,
                courier,
                {
                    "event_id": str(uuid.uuid4()),
                    "shipment_id": sid,
                    "type": "location",
                    "occurred_at": at(minutes),
                    "location": {"latitude": lat, "longitude": 67.0},
                },
            )
        assert (await journey(client, sid))["last_location"]["latitude"] == 24.9

    async def test_an_offline_batch_is_processed_item_by_item_in_occurred_order(self, client):
        courier = uuid.uuid4()
        sid = (await open_journey(client, courier_id=courier))["shipment_id"]
        repeated = milestone(sid, "picked_up", 5)
        await report(client, courier, repeated)
        batch = [
            milestone(sid, "out_for_delivery", 30),
            repeated,
            milestone(sid, "in_transit", 15),
            milestone(sid, "returned", 40),
        ]
        response = await client.post(
            f"{BASE}/updates/batch", json={"updates": batch}, headers=as_courier(courier)
        )
        body = response.json()
        assert (body["recorded"], body["duplicates"], body["rejected"]) == (2, 1, 1)
        assert (await journey(client, sid))["milestone"] == "out_for_delivery"


class TestVisibility:
    async def test_operators_see_only_their_warehouses_journeys(self, client):
        mine = uuid.uuid4()
        sid = (await open_journey(client, warehouse_id=mine))["shipment_id"]
        operator = auth(UserRole.WAREHOUSE_OPERATOR, warehouse_ids=(mine,))
        stranger = auth(UserRole.WAREHOUSE_OPERATOR, warehouse_ids=(uuid.uuid4(),))
        assert (await client.get(f"{BASE}/journeys/{sid}", headers=operator)).status_code == 200
        assert (await client.get(f"{BASE}/journeys/{sid}", headers=stranger)).status_code == 404

    async def test_the_public_view_shows_milestones_but_not_the_courier(self, client):
        courier = uuid.uuid4()
        opened = await open_journey(client, courier_id=courier)
        await report(client, courier, milestone(opened["shipment_id"], "picked_up", 5))
        public = (await client.get(f"{BASE}/public/{opened['reference_no']}")).json()
        assert public["milestone"] == "picked_up"
        assert [t["milestone"] for t in public["timeline"]] == ["dispatched", "picked_up"]
        assert "courier" not in str(public)


async def test_store_is_ready(client):
    assert (await client.get("/health/ready")).json()["checks"] == {"mongo": "ok"}
    assert await store.events.count_documents({}) == 0
    assert journeys.store is store
