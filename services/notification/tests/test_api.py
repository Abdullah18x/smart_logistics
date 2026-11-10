"""The delivery log and suppressions over HTTP."""

from __future__ import annotations

import uuid

from notification.models import Channel
from sl_platform.roles import UserRole
from tests.conftest import (
    assignment_payload,
    auth,
    courier_payload,
    created,
    envelope,
    processor,
    status_changed,
)

SUPPORT = auth(UserRole.CUSTOMER_SUPPORT)


async def test_support_sees_what_a_customer_was_told(client):
    shipment_id = uuid.uuid4()
    await created(shipment_id)
    await status_changed(shipment_id, "dispatched", "dispatching")
    page = (
        await client.get(
            "/api/v1/notifications", params={"shipment_id": str(shipment_id)}, headers=SUPPORT
        )
    ).json()
    assert page["total"] == 3
    assert {item["status"] for item in page["items"]} == {"pending"}
    email = next(i for i in page["items"] if i["template"] == "shipment_dispatched"
                 and i["channel"] == "email")  # fmt: skip
    detail = (await client.get(f"/api/v1/notifications/{email['id']}", headers=SUPPORT)).json()
    assert "has left the warehouse" in detail["body"]
    assert detail["html"].startswith("<!doctype html>")


async def test_couriers_and_warehouse_staff_cannot_read_the_log(client):
    for role in (UserRole.COURIER, UserRole.WAREHOUSE_OPERATOR):
        response = await client.get("/api/v1/notifications", headers=auth(role))
        assert response.status_code == 403


async def test_a_courier_reads_only_their_own_messages(client):
    shipment_id, courier_id = uuid.uuid4(), uuid.uuid4()
    await created(shipment_id)
    await processor.process(envelope("courier.created", courier_payload(courier_id)))
    await processor.process(
        envelope("courier.assigned", assignment_payload(shipment_id, courier_id))
    )
    mine = (
        await client.get(
            "/api/v1/notifications/mine", headers=auth(UserRole.COURIER, courier_id=courier_id)
        )
    ).json()
    assert {i["template"] for i in mine["items"]} == {"new_assignment"} and mine["total"] == 2
    other = (
        await client.get(
            "/api/v1/notifications/mine", headers=auth(UserRole.COURIER, courier_id=uuid.uuid4())
        )
    ).json()
    assert other["total"] == 0


async def test_resend_creates_a_new_entry_and_queues_it(client, recorded, senders):
    from tests.test_sending import send

    shipment_id = uuid.uuid4()
    await created(shipment_id)
    original = recorded.ids[0]
    pending = await client.post(f"/api/v1/notifications/{original}/resend", headers=SUPPORT)
    assert pending.status_code == 409  # still being sent
    await send(original, senders)
    response = await client.post(f"/api/v1/notifications/{original}/resend", headers=SUPPORT)
    assert response.status_code == 202
    copy = response.json()
    assert copy["resend_of"] == str(original) and copy["status"] == "pending"
    assert recorded.ids[-1] == uuid.UUID(copy["id"])


async def test_suppressions_are_managed_by_support_and_removed_by_admins(client):
    body = {"channel": "sms", "address": "+923001234567", "reason": "wrong number"}
    created_ = await client.post("/api/v1/notifications/suppressions", json=body, headers=SUPPORT)
    assert created_.status_code == 201
    duplicate = await client.post("/api/v1/notifications/suppressions", json=body, headers=SUPPORT)
    assert duplicate.status_code == 409
    listed = (await client.get("/api/v1/notifications/suppressions", headers=SUPPORT)).json()
    assert [(s["channel"], s["address"]) for s in listed] == [("sms", "+923001234567")]
    sid = created_.json()["id"]
    assert (
        await client.delete(f"/api/v1/notifications/suppressions/{sid}", headers=SUPPORT)
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/notifications/suppressions/{sid}", headers=auth())
    ).status_code == 204
    assert Channel.SMS.value == "sms"
