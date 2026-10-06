import asyncio
import uuid

from sqlalchemy import func, select

from inventory.db import database
from inventory.events import build_processor
from inventory.models import InventoryReservation, StockMovement
from sl_platform.events import EventEnvelope
from sl_platform.models import OutboxEvent
from sl_platform.roles import UserRole
from tests.conftest import auth, operator

RESERVE = "/api/v1/inventory/reservations"
SUPPORT = auth(UserRole.CUSTOMER_SUPPORT)


def request(warehouse_id, *lines, shipment_id=None):
    return {
        "shipment_id": str(shipment_id or uuid.uuid4()),
        "warehouse_id": str(warehouse_id),
        "lines": [{"sku_id": str(sku.id), "quantity": qty} for sku, qty in lines],
    }


async def event_types():
    async with database.sessionmaker() as session:
        return list(
            (await session.scalars(select(OutboxEvent.event_type).order_by(OutboxEvent.seq))).all()
        )


def event(event_type, **payload):
    return EventEnvelope(event_type=event_type, producer="shipment", key="k", payload=payload)


class TestReserve:
    async def test_holds_stock_and_returns_sku_snapshots(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand=10)
        response = await client.post(RESERVE, json=request(warehouse, (sku, 4)), headers=SUPPORT)
        assert response.status_code == 201
        body = response.json()
        assert body["confirmed"] is False and body["expires_at"] is not None
        assert body["lines"][0] | {"unit_value": None} == {
            "sku_id": str(sku.id),
            "quantity": 4,
            "sku_code": sku.code,
            "sku_name": "Widget",
            "unit_weight_g": 500,
            "unit_value": None,
            "currency": "PKR",
        }
        refreshed = await factory.item(item.id)
        assert (refreshed.reserved_qty, refreshed.available_qty) == (4, 6)
        assert await event_types() == ["stock.reserved"]

    async def test_repeating_the_request_returns_the_same_hold(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku)
        body = request(warehouse, (sku, 3))
        first = await client.post(RESERVE, json=body, headers=SUPPORT)
        again = await client.post(RESERVE, json=body, headers=SUPPORT)
        assert (first.status_code, again.status_code) == (201, 200)
        assert (await factory.item(item.id)).reserved_qty == 3

    async def test_same_shipment_with_different_lines_is_409(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        await factory.stock(warehouse, sku)
        shipment = uuid.uuid4()
        await client.post(
            RESERVE, json=request(warehouse, (sku, 3), shipment_id=shipment), headers=SUPPORT
        )
        other = await client.post(
            RESERVE, json=request(warehouse, (sku, 4), shipment_id=shipment), headers=SUPPORT
        )
        assert other.json()["code"] == "reservation_mismatch"

    async def test_all_or_nothing_when_one_line_is_short(self, client, factory):
        warehouse = await factory.warehouse()
        plenty, scarce = await factory.sku(), await factory.sku()
        plenty_item = await factory.stock(warehouse, plenty, on_hand=50)
        await factory.stock(warehouse, scarce, on_hand=1)
        response = await client.post(
            RESERVE, json=request(warehouse, (plenty, 5), (scarce, 2)), headers=SUPPORT
        )
        assert response.json()["code"] == "insufficient_stock"
        assert (await factory.item(plenty_item.id)).reserved_qty == 0

    async def test_unknown_and_inactive_warehouses_are_refused(self, client, factory):
        sku = await factory.sku()
        unknown = await client.post(RESERVE, json=request(uuid.uuid4(), (sku, 1)), headers=SUPPORT)
        offline = await factory.warehouse(status="maintenance")
        closed = await client.post(RESERVE, json=request(offline, (sku, 1)), headers=SUPPORT)
        assert unknown.status_code == 404
        assert closed.json()["code"] == "warehouse_not_operational"

    async def test_couriers_cannot_hold_stock(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        response = await client.post(
            RESERVE, json=request(warehouse, (sku, 1)), headers=auth(UserRole.COURIER)
        )
        assert response.status_code == 403

    async def test_concurrent_requests_for_the_last_units_cannot_both_win(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand=5)
        results = await asyncio.gather(
            *[
                client.post(RESERVE, json=request(warehouse, (sku, 3)), headers=SUPPORT)
                for _ in range(4)
            ]
        )
        assert sorted(r.status_code for r in results) == [201, 409, 409, 409]
        assert (await factory.item(item.id)).reserved_qty == 3


class TestHoldLifecycle:
    async def test_an_unconfirmed_hold_is_reclaimed_when_the_stock_is_wanted(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand=5)
        abandoned = uuid.uuid4()
        await client.post(
            RESERVE, json=request(warehouse, (sku, 5), shipment_id=abandoned), headers=SUPPORT
        )
        await factory.age_holds(abandoned)
        response = await client.post(RESERVE, json=request(warehouse, (sku, 5)), headers=SUPPORT)
        assert response.status_code == 201
        assert (await factory.item(item.id)).reserved_qty == 5

    async def test_a_confirmed_hold_never_expires_and_dispatch_still_deducts_stock(
        self, client, factory
    ):
        """Regression for the monolith bug: an old hold used to lapse silently,
        and dispatch then committed nothing — on_hand never went down."""
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand=10)
        shipment = uuid.uuid4()
        await client.post(
            RESERVE, json=request(warehouse, (sku, 4), shipment_id=shipment), headers=SUPPORT
        )
        processor = build_processor(database.sessionmaker)
        await processor.process(event("shipment.created", id=str(shipment)))
        await factory.age_holds(shipment, minutes=600)  # no-op: confirmed holds have no expiry
        sweep = await client.post("/api/v1/inventory/release-expired", headers=auth())
        assert sweep.json() == {"released": 0}
        await processor.process(
            event("shipment.status_changed", id=str(shipment), status="dispatched")
        )
        refreshed = await factory.item(item.id)
        assert (refreshed.on_hand_qty, refreshed.reserved_qty) == (6, 0)

    async def test_commit_writes_the_ledger_and_is_idempotent(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        await factory.stock(warehouse, sku, on_hand=10)
        shipment = uuid.uuid4()
        await client.post(
            RESERVE, json=request(warehouse, (sku, 2), shipment_id=shipment), headers=SUPPORT
        )
        first = await client.post(f"{RESERVE}/{shipment}/commit", headers=SUPPORT)
        again = await client.post(f"{RESERVE}/{shipment}/commit", headers=SUPPORT)
        assert (first.json(), again.json()) == ({"committed": 1}, {"committed": 0})
        async with database.sessionmaker() as session:
            deltas = (await session.scalars(select(StockMovement.quantity_delta))).all()
        assert deltas == [-2]

    async def test_commit_without_a_hold_is_409(self, client):
        response = await client.post(f"{RESERVE}/{uuid.uuid4()}/commit", headers=SUPPORT)
        assert response.status_code == 409

    async def test_cancellation_event_releases_the_hold_once(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand=10)
        shipment = uuid.uuid4()
        await client.post(
            RESERVE, json=request(warehouse, (sku, 7), shipment_id=shipment), headers=SUPPORT
        )
        processor = build_processor(database.sessionmaker)
        cancelled = event(
            "shipment.status_changed", id=str(shipment), status="cancelled", reason="Customer"
        )
        assert await processor.process(cancelled) is True
        assert await processor.process(cancelled) is False  # redelivery deduped
        assert (await factory.item(item.id)).reserved_qty == 0
        assert (await event_types())[-1] == "stock.released"

    async def test_concurrent_commit_and_reserve_on_shared_stock_both_finish(self, client, factory):
        """Both paths lock items before holds, so they cannot deadlock."""
        warehouse = await factory.warehouse()
        a, b = await factory.sku(), await factory.sku()
        await factory.stock(warehouse, a, on_hand=50)
        await factory.stock(warehouse, b, on_hand=50)
        shipments = [uuid.uuid4() for _ in range(3)]
        for s in shipments:
            await client.post(
                RESERVE, json=request(warehouse, (a, 2), (b, 2), shipment_id=s), headers=SUPPORT
            )
        results = await asyncio.gather(
            *[client.post(f"{RESERVE}/{s}/commit", headers=SUPPORT) for s in shipments],
            *[
                client.post(RESERVE, json=request(warehouse, (b, 1), (a, 1)), headers=SUPPORT)
                for _ in range(3)
            ],
        )
        assert all(r.status_code in (200, 201) for r in results)


class TestScopes:
    async def test_an_operator_cannot_sweep_another_warehouse(self, client, factory):
        mine, theirs = await factory.warehouse(), await factory.warehouse()
        response = await client.post(
            "/api/v1/inventory/release-expired",
            params={"warehouse_id": str(theirs)},
            headers=operator(mine),
        )
        assert response.status_code == 403

    async def test_operators_list_only_their_stock(self, client, factory):
        mine, theirs = await factory.warehouse(), await factory.warehouse()
        sku = await factory.sku()
        await factory.stock(mine, sku)
        await factory.stock(theirs, sku)
        listed = (await client.get("/api/v1/inventory", headers=operator(mine))).json()
        assert {i["warehouse_id"] for i in listed["items"]} == {str(mine)}

    async def test_adjusting_below_the_held_quantity_is_refused(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand=10)
        await client.post(RESERVE, json=request(warehouse, (sku, 8)), headers=SUPPORT)
        response = await client.post(
            f"/api/v1/inventory/{item.id}/adjustments",
            json={"type": "damage_write_off", "quantity_delta": -5},
            headers=auth(),
        )
        assert response.status_code == 422


class TestWarehouseReplica:
    async def test_snapshots_upsert_and_stale_ones_are_ignored(self, factory):
        processor = build_processor(database.sessionmaker)
        warehouse_id = str(uuid.uuid4())
        base = {
            "id": warehouse_id,
            "code": "KHI-09",
            "name": "K",
            "city": "Karachi",
            "deleted": False,
        }
        await processor.process(
            EventEnvelope(
                event_type="warehouse.created",
                producer="warehouse",
                key=warehouse_id,
                payload=base | {"status": "active", "updated_at": "2026-10-01T10:00:00+00:00"},
            )
        )
        await processor.process(
            EventEnvelope(
                event_type="warehouse.status_changed",
                producer="warehouse",
                key=warehouse_id,
                payload=base | {"status": "maintenance", "updated_at": "2026-10-01T12:00:00+00:00"},
            )
        )
        await processor.process(
            EventEnvelope(  # older snapshot arriving late
                event_type="warehouse.updated",
                producer="warehouse",
                key=warehouse_id,
                payload=base | {"status": "active", "updated_at": "2026-10-01T11:00:00+00:00"},
            )
        )
        from inventory.models import WarehouseRef

        async with database.sessionmaker() as session:
            ref = await session.get(WarehouseRef, uuid.UUID(warehouse_id))
        assert ref.status == "maintenance"

    async def test_holds_are_counted(self, client, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        await factory.stock(warehouse, sku)
        await client.post(RESERVE, json=request(warehouse, (sku, 1)), headers=SUPPORT)
        async with database.sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(InventoryReservation)) == 1
