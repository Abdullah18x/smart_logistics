"""Shipment endpoints: creation with replay protection, and the lifecycle."""

import uuid

import pytest

from app.constants.enums import ShipmentStatus, UserRole

SHIPMENTS = "/api/v1/shipments"
ADDRESS = {
    "contact_name": "Ayesha Khan",
    "contact_phone": "+923001234567",
    "line1": "House 12, Street 4, DHA Phase 6",
    "city": "Karachi",
}


@pytest.fixture
async def world(factory):
    """A support agent who may create shipments, plus stock to draw on."""
    support = await factory.user(UserRole.CUSTOMER_SUPPORT)
    warehouse = await factory.warehouse()
    sku = await factory.sku(weight_g=250)
    item = await factory.stock(warehouse, sku, on_hand_qty=100)
    return support, warehouse, sku, item


def body(warehouse, sku, *, quantity: int = 2, **overrides) -> dict:
    return {
        "origin_warehouse_id": str(warehouse.id),
        "destination_address": ADDRESS,
        "items": [{"sku_id": str(sku.id), "quantity": quantity}],
    } | overrides


class TestCreate:
    async def test_support_creates_a_shipment(self, client, factory, world):
        support, warehouse, sku, _ = world
        response = await client.post(
            SHIPMENTS, json=body(warehouse, sku), headers=factory.auth_headers(support)
        )
        assert response.status_code == 201
        created = response.json()
        assert created["status"] == ShipmentStatus.CREATED.value
        assert created["reference_no"].startswith("SL-")
        assert created["destination_address"]["city"] == "Karachi"
        assert created["items"][0]["sku_code"] == sku.code

    async def test_the_weight_is_computed_server_side(self, client, factory, world):
        support, warehouse, sku, _ = world
        response = await client.post(
            SHIPMENTS, json=body(warehouse, sku, quantity=4), headers=factory.auth_headers(support)
        )
        assert response.json()["total_weight_g"] == 4 * sku.weight_g

    async def test_parcels_are_barcoded_by_the_system(self, client, factory, world):
        support, warehouse, sku, _ = world
        response = await client.post(
            SHIPMENTS,
            json=body(warehouse, sku, packages=[{"weight_g": 1200}]),
            headers=factory.auth_headers(support),
        )
        [parcel] = response.json()["packages"]
        assert parcel["barcode"].startswith("PKG")
        assert parcel["sequence_no"] == 1

    @pytest.mark.parametrize("role", [UserRole.WAREHOUSE_OPERATOR, UserRole.COURIER])
    async def test_operators_and_couriers_cannot_create_shipments(
        self, client, factory, world, role
    ):
        _, warehouse, sku, _ = world
        user = await factory.user(role)
        response = await client.post(
            SHIPMENTS, json=body(warehouse, sku), headers=factory.auth_headers(user)
        )
        assert response.status_code == 403

    async def test_an_unknown_warehouse_is_a_404(self, client, factory, world):
        support, _, sku, _ = world
        payload = {
            "origin_warehouse_id": str(uuid.uuid4()),
            "destination_address": ADDRESS,
            "items": [{"sku_id": str(sku.id), "quantity": 1}],
        }
        response = await client.post(SHIPMENTS, json=payload, headers=factory.auth_headers(support))
        assert response.status_code == 404

    async def test_a_shipment_with_no_items_is_a_422(self, client, factory, world):
        support, warehouse, sku, _ = world
        response = await client.post(
            SHIPMENTS, json=body(warehouse, sku, items=[]), headers=factory.auth_headers(support)
        )
        assert response.status_code == 422

    async def test_more_stock_than_exists_is_a_409(self, client, factory, world):
        support, warehouse, sku, _ = world
        response = await client.post(
            SHIPMENTS,
            json=body(warehouse, sku, quantity=500),
            headers=factory.auth_headers(support),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "insufficient_stock"


class TestIdempotency:
    async def test_a_retry_with_the_same_key_returns_the_original_shipment(
        self, client, factory, world
    ):
        """The whole point: a timed-out client retrying must not create a second one."""
        support, warehouse, sku, item = world
        headers = factory.auth_headers(support) | {"Idempotency-Key": f"key-{uuid.uuid4()}"}
        payload = body(warehouse, sku, quantity=3)

        first = await client.post(SHIPMENTS, json=payload, headers=headers)
        second = await client.post(SHIPMENTS, json=payload, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["reference_no"] == second.json()["reference_no"]
        # And the stock was only taken once.
        assert item.reserved_qty == 3

    async def test_the_same_key_with_a_different_body_is_rejected(self, client, factory, world):
        support, warehouse, sku, _ = world
        headers = factory.auth_headers(support) | {"Idempotency-Key": f"key-{uuid.uuid4()}"}

        await client.post(SHIPMENTS, json=body(warehouse, sku, quantity=1), headers=headers)
        response = await client.post(
            SHIPMENTS, json=body(warehouse, sku, quantity=9), headers=headers
        )
        assert response.status_code == 422
        assert response.json()["code"] == "idempotency_key_reuse"

    async def test_a_failed_request_frees_its_key_for_a_corrected_retry(
        self, client, factory, world
    ):
        support, warehouse, sku, _ = world
        headers = factory.auth_headers(support) | {"Idempotency-Key": f"key-{uuid.uuid4()}"}

        failed = await client.post(
            SHIPMENTS, json=body(warehouse, sku, quantity=500), headers=headers
        )
        assert failed.status_code == 409

        corrected = await client.post(
            SHIPMENTS, json=body(warehouse, sku, quantity=2), headers=headers
        )
        assert corrected.status_code == 201

    async def test_creating_without_a_key_still_works(self, client, factory, world):
        support, warehouse, sku, _ = world
        response = await client.post(
            SHIPMENTS, json=body(warehouse, sku), headers=factory.auth_headers(support)
        )
        assert response.status_code == 201


class TestReadScoping:
    async def test_a_courier_sees_only_their_own_shipments(self, client, factory):
        courier_user = await factory.user(UserRole.COURIER)
        mine = await factory.courier(user=courier_user)
        theirs = await factory.courier()
        warehouse = await factory.warehouse()
        assigned = await factory.shipment(warehouse, courier=mine)
        someone_elses = await factory.shipment(warehouse, courier=theirs)
        headers = factory.auth_headers(courier_user)

        assert (await client.get(f"{SHIPMENTS}/{assigned.id}", headers=headers)).status_code == 200
        other = await client.get(f"{SHIPMENTS}/{someone_elses.id}", headers=headers)
        assert other.status_code == 404

    async def test_a_courier_s_listing_excludes_other_couriers_work(self, client, factory):
        courier_user = await factory.user(UserRole.COURIER)
        mine = await factory.courier(user=courier_user)
        warehouse = await factory.warehouse()
        await factory.shipment(warehouse, courier=mine)
        await factory.shipment(warehouse, courier=await factory.courier())

        response = await client.get(
            SHIPMENTS,
            params={"origin_warehouse_id": str(warehouse.id)},
            headers=factory.auth_headers(courier_user),
        )
        assert response.json()["total"] == 1

    async def test_an_operator_sees_their_warehouse_s_shipments(self, client, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse()
        theirs = await factory.warehouse()
        await factory.assign(operator, mine)
        await factory.shipment(mine)
        hidden = await factory.shipment(theirs)
        headers = factory.auth_headers(operator)

        listing = await client.get(SHIPMENTS, headers=headers)
        assert listing.json()["total"] == 1
        assert (await client.get(f"{SHIPMENTS}/{hidden.id}", headers=headers)).status_code == 404

    async def test_support_sees_everything(self, client, factory):
        support = await factory.user(UserRole.CUSTOMER_SUPPORT)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        response = await client.get(
            f"{SHIPMENTS}/{shipment.id}", headers=factory.auth_headers(support)
        )
        assert response.status_code == 200

    async def test_listing_requires_authentication(self, client):
        assert (await client.get(SHIPMENTS)).status_code == 401


class TestLifecycle:
    async def test_an_operator_marks_their_shipment_ready(self, client, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        await factory.assign(operator, warehouse)
        shipment = await factory.shipment(warehouse)

        response = await client.patch(
            f"{SHIPMENTS}/{shipment.id}/status",
            json={"status": "ready_for_dispatch"},
            headers=factory.auth_headers(operator),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ready_for_dispatch"

    async def test_a_courier_cannot_mark_a_shipment_ready(self, client, factory):
        courier_user = await factory.user(UserRole.COURIER)
        courier = await factory.courier(user=courier_user)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, courier=courier)

        response = await client.patch(
            f"{SHIPMENTS}/{shipment.id}/status",
            json={"status": "ready_for_dispatch"},
            headers=factory.auth_headers(courier_user),
        )
        assert response.status_code == 403

    async def test_a_courier_drives_the_custody_states(self, client, factory):
        courier_user = await factory.user(UserRole.COURIER)
        courier = await factory.courier(user=courier_user)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(
            warehouse, courier=courier, status=ShipmentStatus.DISPATCHED
        )

        response = await client.patch(
            f"{SHIPMENTS}/{shipment.id}/status",
            json={"status": "picked_up"},
            headers=factory.auth_headers(courier_user),
        )
        assert response.status_code == 200
        assert response.json()["picked_up_at"] is not None

    async def test_an_illegal_transition_is_a_409(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)

        response = await client.patch(
            f"{SHIPMENTS}/{shipment.id}/status",
            json={"status": "delivered"},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 409
        assert "Cannot move a shipment" in response.json()["message"]

    async def test_an_unknown_status_is_a_422(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)

        response = await client.patch(
            f"{SHIPMENTS}/{shipment.id}/status",
            json={"status": "teleported"},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 422

    async def test_cancelling_releases_the_held_stock(self, client, factory, world):
        support, warehouse, sku, item = world
        headers = factory.auth_headers(support)
        created = (
            await client.post(SHIPMENTS, json=body(warehouse, sku, quantity=10), headers=headers)
        ).json()
        assert item.reserved_qty == 10

        response = await client.post(
            f"{SHIPMENTS}/{created['id']}/cancel",
            json={"reason": "Customer changed their mind"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert item.reserved_qty == 0

    async def test_cancelling_needs_a_reason(self, client, factory):
        support = await factory.user(UserRole.CUSTOMER_SUPPORT)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)

        response = await client.post(
            f"{SHIPMENTS}/{shipment.id}/cancel",
            json={},
            headers=factory.auth_headers(support),
        )
        assert response.status_code == 422

    async def test_a_dispatched_shipment_can_no_longer_be_edited(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, status=ShipmentStatus.DISPATCHED)

        response = await client.patch(
            f"{SHIPMENTS}/{shipment.id}",
            json={"priority": "critical"},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 409


class TestPacking:
    async def test_an_assigned_operator_adds_a_parcel(self, client, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        await factory.assign(operator, warehouse)
        shipment = await factory.shipment(warehouse)

        response = await client.post(
            f"{SHIPMENTS}/{shipment.id}/packages",
            json={"weight_g": 1500, "length_mm": 400},
            headers=factory.auth_headers(operator),
        )
        assert response.status_code == 201
        assert response.json()["status"] == "pending"

    async def test_support_cannot_pack(self, client, factory):
        """Packing is the warehouse's job, not the call centre's."""
        support = await factory.user(UserRole.CUSTOMER_SUPPORT)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)

        response = await client.post(
            f"{SHIPMENTS}/{shipment.id}/packages",
            json={"weight_g": 1500},
            headers=factory.auth_headers(support),
        )
        assert response.status_code == 403

    async def test_a_zero_weight_parcel_is_a_422(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)

        response = await client.post(
            f"{SHIPMENTS}/{shipment.id}/packages",
            json={"weight_g": 0},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 422

    async def test_marking_a_parcel_packed(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        parcel = await factory.package(shipment)

        response = await client.patch(
            f"{SHIPMENTS}/packages/{parcel.id}",
            json={"status": "packed"},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 200
        assert response.json()["packed_at"] is not None
