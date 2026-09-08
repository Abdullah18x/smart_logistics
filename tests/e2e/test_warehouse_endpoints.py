"""Warehouse endpoints, including the courier navigation view."""

import uuid

import pytest

from app.constants.enums import UserRole, WarehouseStatus

WAREHOUSES = "/api/v1/warehouses"


def new_warehouse_body(**overrides) -> dict:
    return {
        "code": f"E{uuid.uuid4().hex[:7]}",
        "name": "E2E Facility",
        "address_line1": "1 Test Road",
        "city": "Karachi",
        "capacity_units": 10_000,
    } | overrides


class TestCreate:
    async def test_an_admin_creates_a_facility_with_zones_and_hours(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        body = new_warehouse_body(
            zones=[{"code": "a1", "name": "Ambient A1", "capacity_units": 500}],
            operating_hours=[
                {"day_of_week": 1, "opens_at": "08:00:00", "closes_at": "20:00:00"},
                {"day_of_week": 7, "is_closed": True},
            ],
        )

        response = await client.post(WAREHOUSES, json=body, headers=factory.auth_headers(admin))
        assert response.status_code == 201
        created = response.json()
        assert created["code"] == body["code"].upper()
        assert [z["code"] for z in created["zones"]] == ["A1"]
        assert len(created["operating_hours"]) == 2

    @pytest.mark.parametrize(
        "role", [UserRole.WAREHOUSE_OPERATOR, UserRole.CUSTOMER_SUPPORT, UserRole.COURIER]
    )
    async def test_only_admins_may_create_a_facility(self, client, factory, role):
        user = await factory.user(role)
        response = await client.post(
            WAREHOUSES, json=new_warehouse_body(), headers=factory.auth_headers(user)
        )
        assert response.status_code == 403

    async def test_a_duplicate_code_is_a_409(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        body = new_warehouse_body()
        await client.post(WAREHOUSES, json=body, headers=factory.auth_headers(admin))

        response = await client.post(WAREHOUSES, json=body, headers=factory.auth_headers(admin))
        assert response.status_code == 409
        assert response.json()["message"] == "A warehouse with this code already exists."

    async def test_an_impossible_schedule_is_a_422(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        body = new_warehouse_body(
            operating_hours=[{"day_of_week": 1, "opens_at": "20:00:00", "closes_at": "08:00:00"}]
        )
        response = await client.post(WAREHOUSES, json=body, headers=factory.auth_headers(admin))
        assert response.status_code == 422

    async def test_an_unknown_timezone_is_a_422(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        response = await client.post(
            WAREHOUSES,
            json=new_warehouse_body(timezone="Mars/Olympus_Mons"),
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 422


class TestRowScoping:
    async def test_an_operator_sees_their_own_facility(self, client, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        await factory.assign(operator, warehouse)

        response = await client.get(
            f"{WAREHOUSES}/{warehouse.id}", headers=factory.auth_headers(operator)
        )
        assert response.status_code == 200

    async def test_an_operator_is_told_another_facility_does_not_exist(self, client, factory):
        """404, not 403 — its existence is not theirs to learn."""
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse()
        theirs = await factory.warehouse()
        await factory.assign(operator, mine)

        response = await client.get(
            f"{WAREHOUSES}/{theirs.id}", headers=factory.auth_headers(operator)
        )
        assert response.status_code == 404

    async def test_an_operator_s_listing_contains_only_their_facilities(self, client, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse(city="Scopeville")
        await factory.warehouse(city="Scopeville")
        await factory.assign(operator, mine)

        response = await client.get(
            WAREHOUSES, params={"city": "Scopeville"}, headers=factory.auth_headers(operator)
        )
        assert response.json()["total"] == 1

    async def test_an_admin_sees_every_facility_in_the_city(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        await factory.warehouse(city="Adminopolis")
        await factory.warehouse(city="Adminopolis")

        response = await client.get(
            WAREHOUSES, params={"city": "Adminopolis"}, headers=factory.auth_headers(admin)
        )
        assert response.json()["total"] == 2

    async def test_listing_requires_authentication(self, client):
        assert (await client.get(WAREHOUSES)).status_code == 401


class TestCourierNavigationView:
    async def test_it_gives_a_driver_what_they_need_to_reach_the_dock(self, client, factory):
        courier = await factory.user(UserRole.COURIER)
        await factory.warehouse(
            city="Navtown",
            latitude=24.8607,
            longitude=67.0011,
            entrance_latitude=24.8611,
            entrance_longitude=67.0018,
            contact_phone="+922135000002",
        )

        response = await client.get(
            f"{WAREHOUSES}/locations",
            params={"city": "Navtown"},
            headers=factory.auth_headers(courier),
        )
        assert response.status_code == 200
        [location] = response.json()
        assert location["entrance_latitude"] == 24.8611
        assert location["geofence_radius_m"] > 0
        assert location["contact_phone"] == "+922135000002"
        assert "operating_hours" in location

    async def test_it_withholds_commercial_detail(self, client, factory):
        """A driver needs the gate, not the throughput figures."""
        courier = await factory.user(UserRole.COURIER)
        await factory.warehouse(city="Navtown2", capacity_units=99_999)

        response = await client.get(
            f"{WAREHOUSES}/locations",
            params={"city": "Navtown2"},
            headers=factory.auth_headers(courier),
        )
        [location] = response.json()
        for withheld in ("capacity_units", "max_daily_outbound", "notes", "zones"):
            assert withheld not in location

    async def test_facilities_that_are_not_operational_are_omitted(self, client, factory):
        courier = await factory.user(UserRole.COURIER)
        await factory.warehouse(city="Navtown3", status=WarehouseStatus.ACTIVE)
        await factory.warehouse(city="Navtown3", status=WarehouseStatus.MAINTENANCE)

        response = await client.get(
            f"{WAREHOUSES}/locations",
            params={"city": "Navtown3"},
            headers=factory.auth_headers(courier),
        )
        assert len(response.json()) == 1


class TestZones:
    async def test_an_assigned_operator_adds_a_zone(self, client, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        await factory.assign(operator, warehouse)

        response = await client.post(
            f"{WAREHOUSES}/{warehouse.id}/zones",
            json={"code": "b2", "name": "Bulk B2", "capacity_units": 500},
            headers=factory.auth_headers(operator),
        )
        assert response.status_code == 201
        assert response.json()["code"] == "B2"

    async def test_an_unassigned_operator_cannot(self, client, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()

        response = await client.post(
            f"{WAREHOUSES}/{warehouse.id}/zones",
            json={"code": "B3", "name": "Bulk B3", "capacity_units": 500},
            headers=factory.auth_headers(operator),
        )
        assert response.status_code == 403

    async def test_a_courier_cannot_manage_zones(self, client, factory):
        courier = await factory.user(UserRole.COURIER)
        warehouse = await factory.warehouse()
        response = await client.post(
            f"{WAREHOUSES}/{warehouse.id}/zones",
            json={"code": "B4", "name": "Bulk B4", "capacity_units": 500},
            headers=factory.auth_headers(courier),
        )
        assert response.status_code == 403

    async def test_a_duplicate_zone_code_is_a_409(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        headers = factory.auth_headers(admin)
        zone = {"code": "C5", "name": "Cold C5", "capacity_units": 500}
        await client.post(f"{WAREHOUSES}/{warehouse.id}/zones", json=zone, headers=headers)

        response = await client.post(
            f"{WAREHOUSES}/{warehouse.id}/zones", json=zone, headers=headers
        )
        assert response.status_code == 409
        assert "already used in this warehouse" in response.json()["message"]

    async def test_removing_a_zone_is_soft(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        zone = await factory.zone(warehouse)
        headers = factory.auth_headers(admin)

        assert (
            await client.delete(f"{WAREHOUSES}/zones/{zone.id}", headers=headers)
        ).status_code == 204
        # Gone from the API, still in the database for the stock that points at it.
        detail = await client.get(f"{WAREHOUSES}/{warehouse.id}", headers=headers)
        assert zone.id not in [uuid.UUID(z["id"]) for z in detail.json()["zones"]]


class TestOperatingHours:
    async def test_the_week_is_replaced_wholesale(self, client, factory):
        """PUT, not PATCH: an absent day means closed, not unchanged."""
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        for day in range(1, 6):
            await factory.operating_hours(warehouse, day=day)
        headers = factory.auth_headers(admin)

        response = await client.put(
            f"{WAREHOUSES}/{warehouse.id}/operating-hours",
            json={"days": [{"day_of_week": 6, "opens_at": "10:00:00", "closes_at": "14:00:00"}]},
            headers=headers,
        )
        assert response.status_code == 200
        assert [d["day_of_week"] for d in response.json()] == [6]

    async def test_a_repeated_weekday_is_a_422(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        response = await client.put(
            f"{WAREHOUSES}/{warehouse.id}/operating-hours",
            json={
                "days": [
                    {"day_of_week": 1, "is_closed": True},
                    {"day_of_week": 1, "is_closed": True},
                ]
            },
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 422

    async def test_a_courier_cannot_rewrite_the_schedule(self, client, factory):
        courier = await factory.user(UserRole.COURIER)
        warehouse = await factory.warehouse()
        response = await client.put(
            f"{WAREHOUSES}/{warehouse.id}/operating-hours",
            json={"days": [{"day_of_week": 1, "is_closed": True}]},
            headers=factory.auth_headers(courier),
        )
        assert response.status_code == 403


class TestLifecycle:
    async def test_taking_a_facility_offline(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        response = await client.patch(
            f"{WAREHOUSES}/{warehouse.id}/status",
            json={"status": "maintenance", "reason": "Roof repair"},
            headers=factory.auth_headers(admin),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "maintenance"

    async def test_deleting_then_restoring(self, client, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        headers = factory.auth_headers(admin)

        assert (
            await client.delete(f"{WAREHOUSES}/{warehouse.id}", headers=headers)
        ).status_code == 204
        assert (
            await client.get(f"{WAREHOUSES}/{warehouse.id}", headers=headers)
        ).status_code == 404

        restored = await client.post(f"{WAREHOUSES}/{warehouse.id}/restore", headers=headers)
        assert restored.status_code == 200
        assert restored.json()["status"] == WarehouseStatus.ACTIVE.value
