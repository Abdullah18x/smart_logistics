"""End-to-end flows across Identity, Warehouse, Inventory and Shipment."""

import uuid

import pytest

SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")
KHI = uuid.uuid5(SEED_NAMESPACE, "warehouse:KHI-01")
LHE = uuid.uuid5(SEED_NAMESPACE, "warehouse:LHE-01")
KEYBOARD = uuid.uuid5(SEED_NAMESPACE, "sku:SKU-ELEC-0001")
CHARGER = uuid.uuid5(SEED_NAMESPACE, "sku:SKU-ELEC-0003")

pytestmark = pytest.mark.asyncio(loop_scope="session")


def shipment_body(warehouse_id, *lines):
    return {
        "origin_warehouse_id": str(warehouse_id),
        "service_level": "express",
        "destination_address": {
            "contact_name": "Ayesha Khan",
            "contact_phone": "+923001234567",
            "line1": "House 12, Street 4, DHA Phase 6",
            "city": "Karachi",
        },
        "items": [{"sku_id": str(sku), "quantity": qty} for sku, qty in lines],
    }


async def stock(platform, headers, warehouse_id, sku_id) -> dict:
    page = (
        await platform.http["inventory"].get(
            "/api/v1/inventory",
            params={"warehouse_id": str(warehouse_id), "sku_id": str(sku_id)},
            headers=headers,
        )
    ).json()
    return page["items"][0]


async def holds(platform, headers, shipment_id) -> list[dict]:
    return (
        await platform.http["inventory"].get(
            "/api/v1/inventory/reservations", params={"shipment_id": shipment_id}, headers=headers
        )
    ).json()


async def test_replicas_were_built_from_warehouse_events(platform):
    from shipment.db import database
    from shipment.models import WarehouseRef

    async with database.sessionmaker() as session:
        ref = await session.get(WarehouseRef, KHI)
    assert ref.code == "KHI-01" and ref.status == "active"


async def test_identity_token_is_accepted_by_every_other_service(platform):
    support = await platform.login("support.lead@transfleet.com")
    for service, path in (
        ("warehouse", "/api/v1/warehouses"),
        ("inventory", "/api/v1/inventory"),
        ("shipment", "/api/v1/shipments"),
    ):
        assert (await platform.http[service].get(path, headers=support)).status_code == 200


async def test_create_dispatch_and_stock_follows(platform):
    support = await platform.login("support.lead@transfleet.com")
    admin = await platform.login("admin@transfleet.com")
    before = await stock(platform, admin, KHI, KEYBOARD)

    created = await platform.http["shipment"].post(
        "/api/v1/shipments",
        json=shipment_body(KHI, (KEYBOARD, 3), (CHARGER, 1)),
        headers=support | {"Idempotency-Key": "e2e-order-1"},
    )
    assert created.status_code == 201, created.text
    shipment = created.json()
    assert {i["sku_code"] for i in shipment["items"]} == {"SKU-ELEC-0001", "SKU-ELEC-0003"}

    held = await stock(platform, admin, KHI, KEYBOARD)
    assert held["reserved_qty"] == before["reserved_qty"] + 3
    assert {h["confirmed_at"] is None for h in await holds(platform, admin, shipment["id"])} == {
        True
    }

    await platform.pump()  # shipment.created -> Inventory confirms the hold
    assert all(h["confirmed_at"] for h in await holds(platform, admin, shipment["id"]))

    for status in ("ready_for_dispatch", "dispatching", "dispatched"):
        response = await platform.http["shipment"].patch(
            f"/api/v1/shipments/{shipment['id']}/status", json={"status": status}, headers=admin
        )
        assert response.status_code == 200, response.text
    await platform.pump()  # shipment.status_changed(dispatched) -> Inventory commits

    after = await stock(platform, admin, KHI, KEYBOARD)
    assert after["on_hand_qty"] == before["on_hand_qty"] - 3
    assert after["reserved_qty"] == before["reserved_qty"]


async def test_idempotent_retry_across_services_holds_stock_once(platform):
    support = await platform.login("support.lead@transfleet.com")
    admin = await platform.login("admin@transfleet.com")
    before = await stock(platform, admin, LHE, CHARGER)
    headers = support | {"Idempotency-Key": "e2e-retry"}
    body = shipment_body(LHE, (CHARGER, 2))
    first = await platform.http["shipment"].post("/api/v1/shipments", json=body, headers=headers)
    second = await platform.http["shipment"].post("/api/v1/shipments", json=body, headers=headers)
    assert first.json()["id"] == second.json()["id"]
    assert (await stock(platform, admin, LHE, CHARGER))["reserved_qty"] == before[
        "reserved_qty"
    ] + 2


async def test_insufficient_stock_from_inventory_reaches_the_client(platform):
    support = await platform.login("support.lead@transfleet.com")
    response = await platform.http["shipment"].post(
        "/api/v1/shipments", json=shipment_body(KHI, (KEYBOARD, 100_000)), headers=support
    )
    assert response.status_code == 409
    assert response.json()["code"] == "insufficient_stock"


async def test_cancellation_releases_the_hold_via_events(platform):
    support = await platform.login("support.lead@transfleet.com")
    admin = await platform.login("admin@transfleet.com")
    before = await stock(platform, admin, KHI, CHARGER)
    shipment = (
        await platform.http["shipment"].post(
            "/api/v1/shipments", json=shipment_body(KHI, (CHARGER, 4)), headers=support
        )
    ).json()
    await platform.pump()
    await platform.http["shipment"].post(
        f"/api/v1/shipments/{shipment['id']}/cancel",
        json={"reason": "Customer called"},
        headers=support,
    )
    await platform.pump()
    assert (await stock(platform, admin, KHI, CHARGER))["reserved_qty"] == before["reserved_qty"]


async def test_operator_tokens_scope_rows_in_every_service(platform):
    lahore = await platform.login("wh.lahore@transfleet.com")
    shipments = (await platform.http["shipment"].get("/api/v1/shipments", headers=lahore)).json()
    assert {s["origin_warehouse_code"] for s in shipments["items"]} <= {"LHE-01"}
    stock_page = (await platform.http["inventory"].get("/api/v1/inventory", headers=lahore)).json()
    assert {i["warehouse_id"] for i in stock_page["items"]} == {str(LHE)}


async def test_assigning_a_warehouse_is_checked_against_the_warehouse_service(platform):
    admin = await platform.login("admin@transfleet.com")
    users = (
        await platform.http["identity"].get(
            "/api/v1/users", params={"search": "wh.lahore"}, headers=admin
        )
    ).json()["items"]
    operator_id = users[0]["id"]
    ok = await platform.http["identity"].post(
        f"/api/v1/users/{operator_id}/warehouses", json={"warehouse_id": str(KHI)}, headers=admin
    )
    unknown = await platform.http["identity"].post(
        f"/api/v1/users/{operator_id}/warehouses",
        json={"warehouse_id": str(uuid.uuid4())},
        headers=admin,
    )
    assert ok.status_code == 201
    assert unknown.status_code == 404


async def test_warehouse_status_and_deletion_propagate_as_events(platform):
    admin = await platform.login("admin@transfleet.com")
    support = await platform.login("support.lead@transfleet.com")
    isb = uuid.uuid5(SEED_NAMESPACE, "warehouse:ISB-01")
    await platform.http["warehouse"].patch(
        f"/api/v1/warehouses/{isb}/status", json={"status": "maintenance"}, headers=admin
    )
    await platform.pump()
    refused = await platform.http["shipment"].post(
        "/api/v1/shipments", json=shipment_body(isb, (CHARGER, 1)), headers=support
    )
    assert refused.json()["code"] == "warehouse_not_operational"

    # Deleting the warehouse removes operator scopes in Identity — the event
    # that replaces the old cross-schema ON DELETE CASCADE.
    regional = (
        await platform.http["identity"].get(
            "/api/v1/users", params={"search": "wh.regional"}, headers=admin
        )
    ).json()["items"][0]["id"]
    assert (
        await platform.http["warehouse"].delete(f"/api/v1/warehouses/{isb}", headers=admin)
    ).status_code == 204
    await platform.pump()
    scopes = (
        await platform.http["identity"].get(f"/api/v1/users/{regional}/warehouses", headers=admin)
    ).json()
    assert str(isb) not in {s["warehouse_id"] for s in scopes}
