"""Dispatch and delivery end to end: shipment → orchestrator (Temporal) →
inventory, courier, tracking, and back, with every hop over HTTP or events."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.e2e.test_cross_service import (
    CHARGER,
    KEYBOARD,
    KHI,
    LHE,
    dispatch,
    shipment_body,
    stock,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

#: Seeded couriers with a login (CR-0006 is a partner without one).
COURIER_LOGINS = {
    "CR-0001": "courier.one@transfleet.com",
    "CR-0002": "courier.two@transfleet.com",
    "CR-0003": "courier.three@transfleet.com",
}


async def create(platform, warehouse_id, *lines) -> dict:
    support = await platform.login("support.lead@transfleet.com")
    response = await platform.http["shipment"].post(
        "/api/v1/shipments", json=shipment_body(warehouse_id, *lines), headers=support
    )
    assert response.status_code == 201, response.text
    await platform.pump()  # shipment.created -> the hold is confirmed
    return response.json()


async def shipment(platform, headers, shipment_id) -> dict:
    return (
        await platform.http["shipment"].get(f"/api/v1/shipments/{shipment_id}", headers=headers)
    ).json()


async def courier_profile(platform, headers, courier_id) -> dict:
    return (
        await platform.http["courier"].get(f"/api/v1/couriers/{courier_id}", headers=headers)
    ).json()


def field_update(shipment_id, milestone, seconds, **extra) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "shipment_id": shipment_id,
        "type": "milestone",
        "milestone": milestone,
        "occurred_at": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(),
        **extra,
    }


async def test_dispatch_runs_every_step_and_delivery_follows_the_courier(platform):
    admin = await platform.login("admin@transfleet.com")
    before = await stock(platform, admin, KHI, KEYBOARD)
    created = await create(platform, KHI, (KEYBOARD, 2))

    result = await dispatch(platform, admin, created["id"])
    assert result["status"] == "dispatched", result["reason"]
    assert result["steps"][-1] == "start_delivery"

    dispatched = await shipment(platform, admin, created["id"])
    assert dispatched["status"] == "dispatched"
    courier_id = dispatched["courier_id"]
    assert courier_id == result["courier_id"]
    # Labels were printed by the workflow, as PDFs.
    label = await platform.http["shipment"].get(
        dispatched["packages"][0]["label_url"], headers=admin
    )
    assert label.headers["content-type"] == "application/pdf"
    # Stock left the shelf when the shipment was dispatched.
    after = await stock(platform, admin, KHI, KEYBOARD)
    assert after["on_hand_qty"] == before["on_hand_qty"] - 2
    # A Karachi courier carries it, and the journey is open.
    profile = await courier_profile(platform, admin, courier_id)
    assert profile["home_city"] == "Karachi" and profile["active_assignments"] >= 1
    journey = (
        await platform.http["tracking"].get(
            f"/api/v1/tracking/journeys/{created['id']}", headers=admin
        )
    ).json()
    assert journey["status"] == "active" and journey["milestone"] == "dispatched"

    # The courier reports from the field; an offline gap is filled by the workflow.
    courier = await platform.login(COURIER_LOGINS[profile["code"]])
    batch = [
        field_update(created["id"], "picked_up", 1),
        field_update(created["id"], "out_for_delivery", 2),
        field_update(created["id"], "delivered", 3),
    ]
    sent = await platform.http["tracking"].post(
        "/api/v1/tracking/updates/batch", json={"updates": batch}, headers=courier
    )
    assert sent.json()["recorded"] == 3
    delivery = await platform.settle(f"delivery-{created['id']}")
    assert delivery["status"] == "delivered"

    done = await shipment(platform, admin, created["id"])
    assert done["status"] == "delivered"
    history = (
        await platform.http["shipment"].get(
            f"/api/v1/shipments/{created['id']}/history", headers=admin
        )
    ).json()
    assert [h["to_status"] for h in history][-4:] == [
        "picked_up",
        "in_transit",
        "out_for_delivery",
        "delivered",
    ]
    assert {h["actor_role"] for h in history[-4:]} == {"system"}
    # The courier's job is complete and their capacity is back.
    assignments = (
        await platform.http["courier"].get(
            "/api/v1/couriers/assignments", params={"shipment_id": created["id"]}, headers=admin
        )
    ).json()["items"]
    assert [a["status"] for a in assignments] == ["completed"]
    public = (
        await platform.http["tracking"].get(f"/api/v1/tracking/public/{created['reference_no']}")
    ).json()
    assert public["milestone"] == "delivered"


async def test_no_courier_fails_the_dispatch_cleanly_and_a_retry_succeeds(platform):
    admin = await platform.login("admin@transfleet.com")
    lahore_couriers = (
        await platform.http["courier"].get(
            "/api/v1/couriers",
            params={"warehouse_id": str(LHE), "availability": "available"},
            headers=admin,
        )
    ).json()["items"]
    assert lahore_couriers
    for courier in lahore_couriers:  # everyone in Lahore goes offline
        await platform.http["courier"].put(
            f"/api/v1/couriers/{courier['id']}/availability",
            json={"availability": "offline"},
            headers=admin,
        )
    created = await create(platform, LHE, (CHARGER, 1))

    failed = await dispatch(platform, admin, created["id"])
    assert failed["status"] == "dispatch_failed"
    assert failed["reason"].startswith("no_courier_available")
    after_failure = await shipment(platform, admin, created["id"])
    assert after_failure["status"] == "dispatch_failed"
    assert after_failure["failure_reason"].startswith("no_courier_available")
    assert after_failure["courier_id"] is None
    # The stock hold survives a failed dispatch, ready for the retry.
    holds = (
        await platform.http["inventory"].get(
            "/api/v1/inventory/reservations", params={"shipment_id": created["id"]}, headers=admin
        )
    ).json()
    assert [h["status"] for h in holds] == ["held"]

    # A courier comes on shift; operations retry: a new attempt, a new workflow.
    await platform.http["courier"].put(
        f"/api/v1/couriers/{lahore_couriers[0]['id']}/availability",
        json={"availability": "available"},
        headers=admin,
    )
    http = platform.http["shipment"]
    await http.patch(
        f"/api/v1/shipments/{created['id']}/status",
        json={"status": "ready_for_dispatch"},
        headers=admin,
    )
    retry = await http.post(f"/api/v1/shipments/{created['id']}/dispatch", headers=admin)
    assert retry.json()["dispatch_workflow_id"].endswith("-2")
    await platform.pump()
    result = await platform.settle(retry.json()["dispatch_workflow_id"])
    assert result["status"] == "dispatched"
    assert result["courier_id"] == lahore_couriers[0]["id"]


async def test_a_double_click_dispatches_once(platform):
    admin = await platform.login("admin@transfleet.com")
    created = await create(platform, KHI, (CHARGER, 1))
    http = platform.http["shipment"]
    await http.patch(
        f"/api/v1/shipments/{created['id']}/status",
        json={"status": "ready_for_dispatch"},
        headers=admin,
    )
    await http.post(
        f"/api/v1/shipments/{created['id']}/packages", json={"weight_g": 500}, headers=admin
    )
    first = await http.post(f"/api/v1/shipments/{created['id']}/dispatch", headers=admin)
    second = await http.post(f"/api/v1/shipments/{created['id']}/dispatch", headers=admin)
    assert first.status_code == 202 and second.status_code == 409
    await platform.pump()
    result = await platform.settle(first.json()["dispatch_workflow_id"])
    assert result["status"] == "dispatched"
    assignments = (
        await platform.http["courier"].get(
            "/api/v1/couriers/assignments", params={"shipment_id": created["id"]}, headers=admin
        )
    ).json()["items"]
    assert len(assignments) == 1
