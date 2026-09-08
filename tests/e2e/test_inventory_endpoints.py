"""Stock endpoints: levels, holds, and the expiry sweep."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import ReservationStatus, UserRole

INVENTORY = "/api/v1/inventory"


class TestStockLevels:
    async def test_an_admin_sees_stock_across_the_network(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        await factory.stock(warehouse, sku, on_hand_qty=40, reserved_qty=15)

        response = await client.get(
            INVENTORY,
            params={"warehouse_id": str(warehouse.id)},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 200
        [position] = response.json()["items"]
        assert position["on_hand_qty"] == 40
        assert position["reserved_qty"] == 15
        assert position["available_qty"] == 25

    async def test_an_operator_only_sees_their_own_warehouses(self, client, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse()
        theirs = await factory.warehouse()
        await factory.assign(operator, mine)
        await factory.stock(mine, await factory.sku())
        await factory.stock(theirs, await factory.sku())

        response = await client.get(INVENTORY, headers=factory.auth_headers(operator))
        assert response.json()["total"] == 1

    async def test_an_unassigned_operator_sees_nothing(self, client, factory):
        """Empty scope means no rows, not all rows."""
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        await factory.stock(warehouse, await factory.sku())

        response = await client.get(INVENTORY, headers=factory.auth_headers(operator))
        assert response.json()["total"] == 0

    async def test_a_courier_cannot_read_stock_levels(self, client, factory):
        courier = await factory.user(UserRole.COURIER)
        response = await client.get(INVENTORY, headers=factory.auth_headers(courier))
        assert response.status_code == 403

    async def test_the_low_stock_filter_finds_positions_at_their_threshold(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        await factory.stock(warehouse, await factory.sku(), on_hand_qty=5, reorder_level=10)
        await factory.stock(warehouse, await factory.sku(), on_hand_qty=500, reorder_level=10)

        response = await client.get(
            INVENTORY,
            params={"warehouse_id": str(warehouse.id), "below_reorder_level": True},
            headers=factory.auth_headers(admin),
        )
        assert response.json()["total"] == 1

    async def test_the_in_stock_filter_excludes_fully_held_positions(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        await factory.stock(warehouse, await factory.sku(), on_hand_qty=10, reserved_qty=10)
        await factory.stock(warehouse, await factory.sku(), on_hand_qty=10, reserved_qty=0)

        response = await client.get(
            INVENTORY,
            params={"warehouse_id": str(warehouse.id), "in_stock_only": True},
            headers=factory.auth_headers(admin),
        )
        assert response.json()["total"] == 1

    async def test_reading_stock_requires_authentication(self, client):
        assert (await client.get(INVENTORY)).status_code == 401


class TestReservations:
    async def test_holds_can_be_listed_for_one_shipment(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=10, reserved_qty=4)
        shipment = await factory.shipment(warehouse)
        await factory.reservation(item, shipment.id, quantity=4)

        response = await client.get(
            f"{INVENTORY}/reservations",
            params={"shipment_id": str(shipment.id)},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 200
        [hold] = response.json()
        assert hold["quantity"] == 4
        assert hold["status"] == ReservationStatus.HELD.value
        assert hold["expires_at"] is not None

    async def test_holds_can_be_filtered_by_status(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=10, reserved_qty=2)
        shipment = await factory.shipment(warehouse)
        await factory.reservation(item, shipment.id, quantity=2)
        await factory.reservation(item, shipment.id, quantity=1, status=ReservationStatus.RELEASED)

        response = await client.get(
            f"{INVENTORY}/reservations",
            params={"shipment_id": str(shipment.id), "status": "released"},
            headers=factory.auth_headers(admin),
        )
        assert len(response.json()) == 1

    async def test_an_operator_only_sees_holds_at_their_own_warehouses(self, client, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        theirs = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(theirs, sku, on_hand_qty=10, reserved_qty=3)
        shipment = await factory.shipment(theirs)
        await factory.reservation(item, shipment.id, quantity=3)

        response = await client.get(
            f"{INVENTORY}/reservations", headers=factory.auth_headers(operator)
        )
        assert response.json() == []

    async def test_a_courier_cannot_read_holds(self, client, factory):
        courier = await factory.user(UserRole.COURIER)
        response = await client.get(
            f"{INVENTORY}/reservations", headers=factory.auth_headers(courier)
        )
        assert response.status_code == 403


class TestExpirySweep:
    async def test_an_admin_can_release_lapsed_holds(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=10, reserved_qty=6)
        shipment = await factory.shipment(warehouse)
        await factory.reservation(
            item, shipment.id, quantity=6, expires_at=datetime.now(UTC) - timedelta(minutes=5)
        )

        response = await client.post(
            f"{INVENTORY}/release-expired",
            params={"warehouse_id": str(warehouse.id)},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 200
        assert response.json() == {"released": 1}
        assert item.reserved_qty == 0

    async def test_live_holds_are_left_alone(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=10, reserved_qty=6)
        shipment = await factory.shipment(warehouse)
        await factory.reservation(item, shipment.id, quantity=6)

        response = await client.post(
            f"{INVENTORY}/release-expired",
            params={"warehouse_id": str(warehouse.id)},
            headers=factory.auth_headers(admin),
        )
        assert response.json() == {"released": 0}

    async def test_an_operator_sweeps_only_their_own_facilities(self, client, factory):
        """No warehouse given, so it falls back to their assignments — not everything."""
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse()
        theirs = await factory.warehouse()
        await factory.assign(operator, mine)
        expired_at = datetime.now(UTC) - timedelta(minutes=5)

        for warehouse in (mine, theirs):
            sku = await factory.sku()
            item = await factory.stock(warehouse, sku, on_hand_qty=5, reserved_qty=5)
            shipment = await factory.shipment(warehouse)
            await factory.reservation(item, shipment.id, quantity=5, expires_at=expired_at)

        response = await client.post(
            f"{INVENTORY}/release-expired", headers=factory.auth_headers(operator)
        )
        assert response.json() == {"released": 1}

    @pytest.mark.parametrize("role", [UserRole.COURIER, UserRole.CUSTOMER_SUPPORT])
    async def test_couriers_and_support_cannot_sweep(self, client, factory, role):
        user = await factory.user(role)
        response = await client.post(
            f"{INVENTORY}/release-expired", headers=factory.auth_headers(user)
        )
        assert response.status_code == 403
