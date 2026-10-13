"""Requesting a dispatch, labels, and who may record the outcome."""

import uuid
from io import BytesIO

from pypdf import PdfReader

from shipment.db import database
from sl_platform.roles import UserRole
from tests.conftest import auth, operator
from tests.factories import add_warehouse
from tests.test_shipments import ADMIN, BASE, SYSTEM, create, dispatch, move, outbox


async def ready_with_package(client, warehouse_id=None) -> dict:
    created = await create(client, warehouse_id)
    await move(client, created["id"], "ready_for_dispatch")
    await client.post(f"{BASE}/{created['id']}/packages", json={"weight_g": 900}, headers=ADMIN)
    return created


class TestRequestDispatch:
    async def test_request_moves_to_dispatching_and_emits_the_workflow_trigger(
        self, client, inventory
    ):
        created = await ready_with_package(client)
        response = await client.post(f"{BASE}/{created['id']}/dispatch", headers=ADMIN)
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "dispatching"
        assert body["dispatch_attempts"] == 1
        assert body["dispatch_workflow_id"] == f"dispatch-{created['id']}-1"
        requested = [p for t, p in await outbox() if t == "shipment.dispatch_requested"]
        assert requested[-1]["workflow_id"] == body["dispatch_workflow_id"]
        assert requested[-1]["attempt"] == 1

    async def test_a_second_click_is_refused_while_dispatching(self, client, inventory):
        created = await ready_with_package(client)
        await client.post(f"{BASE}/{created['id']}/dispatch", headers=ADMIN)
        again = await client.post(f"{BASE}/{created['id']}/dispatch", headers=ADMIN)
        assert again.status_code == 409
        requested = [p for t, p in await outbox() if t == "shipment.dispatch_requested"]
        assert len(requested) == 1

    async def test_a_retry_after_failure_is_a_new_attempt(self, client, inventory):
        created = await ready_with_package(client)
        await client.post(f"{BASE}/{created['id']}/dispatch", headers=ADMIN)
        failed = await move(
            client, created["id"], "dispatch_failed", headers=SYSTEM, reason="no courier"
        )
        assert failed.json()["failure_reason"] == "no courier"
        await move(client, created["id"], "ready_for_dispatch")
        retry = await client.post(f"{BASE}/{created['id']}/dispatch", headers=ADMIN)
        assert retry.json()["dispatch_workflow_id"] == f"dispatch-{created['id']}-2"
        assert retry.json()["failure_reason"] is None

    async def test_nothing_to_dispatch_without_packages(self, client, inventory):
        created = await create(client)
        await move(client, created["id"], "ready_for_dispatch")
        response = await client.post(f"{BASE}/{created['id']}/dispatch", headers=ADMIN)
        assert response.status_code == 409

    async def test_operators_dispatch_only_their_warehouses(self, client, inventory):
        mine = await add_warehouse(database.sessionmaker)
        theirs = await ready_with_package(client)
        own = await ready_with_package(client, mine)
        assert (
            await client.post(f"{BASE}/{own['id']}/dispatch", headers=operator(mine))
        ).status_code == 202
        assert (
            await client.post(f"{BASE}/{theirs['id']}/dispatch", headers=operator(mine))
        ).status_code == 404
        support = auth(UserRole.CUSTOMER_SUPPORT)
        assert (
            await client.post(f"{BASE}/{theirs['id']}/dispatch", headers=support)
        ).status_code == 403


class TestOutcomeBelongsToTheWorkflow:
    async def test_nobody_but_the_workflow_marks_dispatched(self, client, inventory):
        created = await ready_with_package(client)
        assert (await move(client, created["id"], "dispatching")).status_code == 403
        await client.post(f"{BASE}/{created['id']}/dispatch", headers=ADMIN)
        assert (await move(client, created["id"], "dispatched")).status_code == 403
        assert (await move(client, created["id"], "dispatch_failed")).status_code == 403

    async def test_the_workflow_records_the_courier_with_dispatched(self, client, inventory):
        created = await ready_with_package(client)
        await client.post(f"{BASE}/{created['id']}/dispatch", headers=ADMIN)
        courier_id = str(uuid.uuid4())
        done = await client.patch(
            f"{BASE}/{created['id']}/status",
            json={"status": "dispatched", "courier_id": courier_id},
            headers=SYSTEM,
        )
        body = done.json()
        assert body["courier_id"] == courier_id
        assert {p["status"] for p in body["packages"]} == {"dispatched"}

    async def test_only_the_workflow_may_set_the_courier(self, client, inventory):
        created = await create(client)
        response = await client.patch(
            f"{BASE}/{created['id']}/status",
            json={"status": "ready_for_dispatch", "courier_id": str(uuid.uuid4())},
            headers=ADMIN,
        )
        assert response.status_code == 403


class TestLabels:
    async def test_labels_are_pdfs_with_the_barcode_and_can_be_reprinted(self, client, inventory):
        created = await ready_with_package(client)
        await client.post(f"{BASE}/{created['id']}/packages", json={"weight_g": 400}, headers=ADMIN)
        labelled = await client.post(f"{BASE}/{created['id']}/labels", headers=SYSTEM)
        assert labelled.status_code == 200
        packages = labelled.json()["packages"]
        assert len(packages) == 2
        assert {p["status"] for p in packages} == {"labelled"}
        pdf = await client.get(packages[0]["label_url"], headers=ADMIN)
        assert pdf.headers["content-type"] == "application/pdf"
        text = PdfReader(BytesIO(pdf.content)).pages[0].extract_text()
        assert packages[0]["barcode"] in text
        assert created["reference_no"] in text
        again = await client.post(f"{BASE}/{created['id']}/labels", headers=SYSTEM)
        assert again.status_code == 200

    async def test_labels_are_generated_by_the_workflow_not_by_staff(self, client, inventory):
        created = await ready_with_package(client)
        response = await client.post(
            f"{BASE}/{created['id']}/labels", headers=auth(UserRole.CUSTOMER_SUPPORT)
        )
        assert response.status_code == 403

    async def test_no_label_yet_is_404(self, client, inventory):
        created = await ready_with_package(client)
        detail = (await client.get(f"{BASE}/{created['id']}", headers=ADMIN)).json()
        package_id = detail["packages"][0]["id"]
        response = await client.get(f"{BASE}/packages/{package_id}/label", headers=ADMIN)
        assert response.status_code == 404


async def test_full_dispatch_helper_still_commits(client, inventory):
    created = await create(client)
    assert (await dispatch(client, created["id"]))["status"] == "dispatched"


class TestDeliveryBelongsToTheWorkflow:
    async def test_field_statuses_are_written_only_by_the_workflow(self, client, inventory):
        created = await create(client)
        await dispatch(client, created["id"])
        courier = auth(UserRole.COURIER, courier_id=uuid.uuid4())
        for headers in (ADMIN, courier):
            assert (
                await move(client, created["id"], "picked_up", headers=headers)
            ).status_code in (
                403,
                404,
            )
        for status in ("picked_up", "in_transit", "out_for_delivery"):
            assert (await move(client, created["id"], status, headers=SYSTEM)).status_code == 200
        failed = await move(
            client, created["id"], "delivery_failed", headers=SYSTEM, reason="Nobody home"
        )
        assert failed.json()["delivery_attempt_count"] == 1

    async def test_support_can_still_start_a_return(self, client, inventory):
        created = await create(client)
        await dispatch(client, created["id"])
        for status in ("picked_up", "in_transit", "out_for_delivery"):
            await move(client, created["id"], status, headers=SYSTEM)
        await move(client, created["id"], "delivery_failed", headers=SYSTEM, reason="Closed")
        support = auth(UserRole.CUSTOMER_SUPPORT)
        started = await move(client, created["id"], "return_initiated", headers=support)
        assert started.status_code == 200
