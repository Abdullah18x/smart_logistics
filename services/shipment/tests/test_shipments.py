import uuid

from sqlalchemy import func, select

from shipment.db import database
from shipment.events import build_processor
from shipment.models import Shipment, WarehouseRef
from sl_platform.errors import UpstreamUnavailableError
from sl_platform.events import EventEnvelope
from sl_platform.models import OutboxEvent
from sl_platform.roles import UserRole
from tests.conftest import auth, operator
from tests.factories import FakeInventory, add_warehouse, assign_courier

BASE = "/api/v1/shipments"
ADMIN_ID = uuid.uuid4()
ADMIN = auth(UserRole.ADMIN, user_id=ADMIN_ID)
SUPPORT = auth(UserRole.CUSTOMER_SUPPORT)


def body(warehouse_id, *skus, **overrides):
    return {
        "origin_warehouse_id": str(warehouse_id),
        "destination_address": {
            "contact_name": "Ayesha Khan",
            "contact_phone": "+923001234567",
            "line1": "House 12, Street 4",
            "city": "Karachi",
        },
        "items": [{"sku_id": str(s), "quantity": 2} for s in (skus or [uuid.uuid4()])],
        **overrides,
    }


async def create(client, warehouse_id=None, headers=ADMIN, **overrides) -> dict:
    warehouse_id = warehouse_id or await add_warehouse(database.sessionmaker)
    response = await client.post(BASE, json=body(warehouse_id, **overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def move(client, shipment_id, status, headers=ADMIN, **extra):
    return await client.patch(
        f"{BASE}/{shipment_id}/status", json={"status": status, **extra}, headers=headers
    )


async def outbox():
    async with database.sessionmaker() as session:
        return [
            (r.event_type, r.envelope["payload"])
            for r in (await session.scalars(select(OutboxEvent).order_by(OutboxEvent.seq))).all()
        ]


async def shipment_count():
    async with database.sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(Shipment))


class TestCreate:
    async def test_reserves_then_records_with_snapshots_history_and_event(self, client, inventory):
        warehouse = await add_warehouse(database.sessionmaker)
        sku = uuid.uuid4()
        created = await create(client, warehouse, items=[{"sku_id": str(sku), "quantity": 3}])
        assert created["reference_no"].startswith("SL-")
        assert created["items"][0]["sku_name"] == "Widget"
        assert created["total_weight_g"] == 750
        assert created["origin_warehouse_code"].startswith("W-")
        assert uuid.UUID(created["id"]) in inventory.reserved
        assert inventory.tokens[0] == ADMIN["Authorization"].split()[1]  # caller's token forwarded
        [(event_type, payload)] = await outbox()
        assert event_type == "shipment.created" and payload["items"][0]["quantity"] == 3
        history = (await client.get(f"{BASE}/{created['id']}/history", headers=ADMIN)).json()
        assert [h["to_status"] for h in history] == ["created"]

    async def test_an_inactive_warehouse_is_refused_without_calling_inventory(
        self, client, inventory
    ):
        offline = await add_warehouse(database.sessionmaker, status="maintenance")
        response = await client.post(BASE, json=body(offline), headers=ADMIN)
        unknown = await client.post(BASE, json=body(uuid.uuid4()), headers=ADMIN)
        assert response.json()["code"] == "warehouse_not_operational"
        assert unknown.status_code == 404
        assert inventory.tokens == []

    async def test_inventorys_insufficient_stock_reaches_the_client_unchanged(
        self, client, inventory
    ):
        inventory.fail_with = FakeInventory.insufficient()
        warehouse = await add_warehouse(database.sessionmaker)
        response = await client.post(BASE, json=body(warehouse), headers=ADMIN)
        assert response.status_code == 409 and response.json()["code"] == "insufficient_stock"
        assert await shipment_count() == 0

    async def test_inventory_down_is_503_and_nothing_is_stored(self, client, inventory):
        inventory.fail_with = UpstreamUnavailableError("inventory is unavailable.")
        warehouse = await add_warehouse(database.sessionmaker)
        response = await client.post(BASE, json=body(warehouse), headers=ADMIN)
        assert response.status_code == 503
        assert await shipment_count() == 0

    async def test_a_failure_after_reserving_releases_the_hold(self, client, inventory):
        inventory.duplicate_lines = True  # makes the insert violate uq_shipment_item_sku
        warehouse = await add_warehouse(database.sessionmaker)
        response = await client.post(BASE, json=body(warehouse), headers=ADMIN)
        assert response.status_code == 409
        assert len(inventory.released) == 1
        assert await shipment_count() == 0 and await outbox() == []

    async def test_duplicate_skus_in_the_request_are_422(self, client, inventory):
        warehouse = await add_warehouse(database.sessionmaker)
        sku = uuid.uuid4()
        response = await client.post(BASE, json=body(warehouse, sku, sku), headers=ADMIN)
        assert response.status_code == 422

    async def test_couriers_and_operators_cannot_create(self, client, inventory):
        warehouse = await add_warehouse(database.sessionmaker)
        for headers in (auth(UserRole.COURIER), operator(warehouse)):
            assert (
                await client.post(BASE, json=body(warehouse), headers=headers)
            ).status_code == 403


class TestIdempotentCreate:
    async def test_a_retry_returns_the_same_shipment_and_reuses_the_hold(self, client, inventory):
        warehouse = await add_warehouse(database.sessionmaker)
        headers = ADMIN | {"Idempotency-Key": "order-88123"}
        payload = body(warehouse)
        first = await client.post(BASE, json=payload, headers=headers)
        second = await client.post(BASE, json=payload, headers=headers)
        assert (first.status_code, second.status_code) == (201, 201)
        assert first.json()["id"] == second.json()["id"]
        assert len(inventory.reserved) == 1 and await shipment_count() == 1

    async def test_after_a_failed_attempt_the_retry_targets_the_same_hold(self, client, inventory):
        """The shipment id is derived from (caller, key), so the retry asks
        Inventory for the same hold instead of taking a second one."""
        warehouse = await add_warehouse(database.sessionmaker)
        headers = ADMIN | {"Idempotency-Key": "order-1"}
        payload = body(warehouse)
        inventory.fail_with = UpstreamUnavailableError("down")
        assert (await client.post(BASE, json=payload, headers=headers)).status_code == 503
        inventory.fail_with = None
        retry = await client.post(BASE, json=payload, headers=headers)
        assert retry.status_code == 201
        assert list(inventory.reserved) == [uuid.UUID(retry.json()["id"])]


class TestLifecycle:
    async def test_transitions_follow_the_state_machine_and_write_history(self, client, inventory):
        created = await create(client)
        for status in ("ready_for_dispatch", "dispatching", "dispatched"):
            assert (await move(client, created["id"], status)).status_code == 200
        illegal = await move(client, created["id"], "delivered")
        assert illegal.status_code == 409
        history = (await client.get(f"{BASE}/{created['id']}/history", headers=ADMIN)).json()
        assert [h["to_status"] for h in history] == [
            "created",
            "ready_for_dispatch",
            "dispatching",
            "dispatched",
        ]
        status_events = [p for t, p in await outbox() if t == "shipment.status_changed"]
        assert (
            status_events[-1]["status"] == "dispatched"
            and status_events[-1]["previous_status"] == "dispatching"
        )

    async def test_cancellation_is_refused_once_dispatched(self, client, inventory):
        created = await create(client)
        for status in ("ready_for_dispatch", "dispatching", "dispatched"):
            await move(client, created["id"], status)
        response = await client.post(
            f"{BASE}/{created['id']}/cancel", json={"reason": "Changed mind"}, headers=ADMIN
        )
        assert response.status_code == 409

    async def test_cancel_emits_the_event_inventory_releases_on(self, client, inventory):
        created = await create(client)
        response = await client.post(
            f"{BASE}/{created['id']}/cancel", json={"reason": "Customer called"}, headers=SUPPORT
        )
        assert response.json()["status"] == "cancelled"
        event_type, payload = (await outbox())[-1]
        assert (event_type, payload["status"], payload["reason"]) == (
            "shipment.status_changed",
            "cancelled",
            "Customer called",
        )

    async def test_concurrent_transitions_cannot_both_apply(self, client, inventory):
        """Pod A reads the shipment, pod B changes it and commits, then pod A
        writes: the versioned UPDATE matches nothing and A's change is refused."""
        import pytest
        from sqlalchemy.orm.exc import StaleDataError

        created = await create(client)
        shipment_id = uuid.UUID(created["id"])
        async with database.sessionmaker() as pod_a:
            stale = await pod_a.get(Shipment, shipment_id)  # A has read version 1
            assert (await move(client, created["id"], "ready_for_dispatch")).status_code == 200  # B
            stale.priority = "high"
            with pytest.raises(StaleDataError):
                await pod_a.flush()
        final = (await client.get(f"{BASE}/{created['id']}", headers=ADMIN)).json()
        assert (final["status"], final["version"], final["priority"]) == (
            "ready_for_dispatch",
            2,
            "normal",
        )

    async def test_a_concurrent_update_surfaces_as_409(self, client, inventory):
        """The API maps the stale write to 409 concurrent_update."""
        from unittest.mock import patch

        from sqlalchemy.orm.exc import StaleDataError

        created = await create(client)
        with patch(
            "shipment.services.shipments.ShipmentService._record", side_effect=StaleDataError("x")
        ):
            response = await move(client, created["id"], "ready_for_dispatch")
        assert response.status_code == 409 and response.json()["code"] == "concurrent_update"

    async def test_a_stale_expected_version_is_409(self, client, inventory):
        created = await create(client)
        response = await client.patch(
            f"{BASE}/{created['id']}/status",
            params={"version": 99},
            json={"status": "ready_for_dispatch"},
            headers=ADMIN,
        )
        assert response.json()["code"] == "concurrent_update"

    async def test_roles_own_their_part_of_the_lifecycle(self, client, inventory):
        warehouse = await add_warehouse(database.sessionmaker)
        created = await create(client, warehouse)
        assert (
            await move(client, created["id"], "ready_for_dispatch", auth(UserRole.COURIER))
        ).status_code == 404
        other_operator = operator(uuid.uuid4())
        assert (
            await move(client, created["id"], "ready_for_dispatch", other_operator)
        ).status_code == 404
        assert (
            await move(client, created["id"], "ready_for_dispatch", operator(warehouse))
        ).status_code == 200


class TestUpdate:
    async def test_a_new_service_level_moves_the_promise(self, client, inventory):
        created = await create(client, service_level="economy")
        updated = await client.patch(
            f"{BASE}/{created['id']}", json={"service_level": "express"}, headers=ADMIN
        )
        assert updated.json()["promised_delivery_at"] < created["promised_delivery_at"]

    async def test_required_fields_cannot_be_cleared(self, client, inventory):
        created = await create(client)
        response = await client.patch(
            f"{BASE}/{created['id']}", json={"priority": None}, headers=ADMIN
        )
        assert response.status_code == 409

    async def test_no_edits_after_dispatch(self, client, inventory):
        created = await create(client)
        for status in ("ready_for_dispatch", "dispatching", "dispatched"):
            await move(client, created["id"], status)
        response = await client.patch(
            f"{BASE}/{created['id']}", json={"priority": "high"}, headers=ADMIN
        )
        assert response.status_code == 409


class TestScoping:
    async def test_operators_list_only_their_warehouses(self, client, inventory):
        mine = await add_warehouse(database.sessionmaker)
        await create(client, mine)
        await create(client)
        listed = (await client.get(BASE, headers=operator(mine))).json()
        assert listed["total"] == 1

    async def test_couriers_see_only_their_assignments(self, client, inventory):
        courier_id = uuid.uuid4()
        mine, theirs = await create(client), await create(client)
        await assign_courier(database.sessionmaker, uuid.UUID(mine["id"]), courier_id)
        headers = auth(UserRole.COURIER, courier_id=courier_id)
        assert (await client.get(BASE, headers=headers)).json()["total"] == 1
        assert (await client.get(f"{BASE}/{theirs['id']}", headers=headers)).status_code == 404
        no_profile = auth(UserRole.COURIER)
        assert (await client.get(BASE, headers=no_profile)).json()["total"] == 0

    async def test_packing_is_the_origin_warehouses_job(self, client, inventory):
        warehouse = await add_warehouse(database.sessionmaker)
        created = await create(client, warehouse)
        parcel = {"weight_g": 1200}
        ok = await client.post(
            f"{BASE}/{created['id']}/packages", json=parcel, headers=operator(warehouse)
        )
        denied = await client.post(f"{BASE}/{created['id']}/packages", json=parcel, headers=SUPPORT)
        assert ok.status_code == 201 and ok.json()["barcode"].startswith("PKG")
        assert denied.status_code == 403


class TestWarehouseReplica:
    async def test_warehouse_events_build_the_local_copy(self):
        processor = build_processor(database.sessionmaker)
        warehouse_id = str(uuid.uuid4())
        await processor.process(
            EventEnvelope(
                event_type="warehouse.created",
                producer="warehouse",
                key=warehouse_id,
                payload={
                    "id": warehouse_id,
                    "code": "LHE-09",
                    "name": "L",
                    "city": "Lahore",
                    "status": "active",
                    "deleted": False,
                    "updated_at": "2026-10-06T10:00:00+00:00",
                },
            )
        )
        async with database.sessionmaker() as session:
            ref = await session.get(WarehouseRef, uuid.UUID(warehouse_id))
        assert ref.code == "LHE-09" and ref.is_operational
