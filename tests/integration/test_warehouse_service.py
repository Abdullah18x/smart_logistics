"""Warehouse management and the row-level scoping that guards it.

The rule under test throughout: an operator who is not assigned to a facility is
told it does not exist, never that they are forbidden. Whether a warehouse
exists is itself information they are not entitled to (ADR-009).
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.enums import UserRole, WarehouseStatus, WarehouseType, ZoneType
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.warehouse import Warehouse
from app.models.warehouse_zone import WarehouseZone
from app.schemas.warehouse import WarehouseCreate, WarehouseStatusUpdate, WarehouseUpdate
from app.schemas.warehouse_operating_hours import WeeklyScheduleUpdate
from app.schemas.warehouse_zone import WarehouseZoneCreate, WarehouseZoneUpdate
from app.services.warehouse_service import WarehouseService


@pytest.fixture
def warehouses(db_session) -> WarehouseService:
    return WarehouseService(db_session)


def creation_payload(**overrides) -> WarehouseCreate:
    return WarehouseCreate(
        **{
            "code": f"T{uuid.uuid4().hex[:7]}",
            "name": "Test Facility",
            "address_line1": "1 Test Road",
            "city": "Karachi",
            "capacity_units": 10_000,
        }
        | overrides
    )


class TestCreate:
    async def test_creates_an_active_facility(self, warehouses):
        warehouse = await warehouses.create(creation_payload())
        assert warehouse.status is WarehouseStatus.ACTIVE
        assert warehouse.type is WarehouseType.FULFILLMENT_CENTER

    async def test_the_code_is_stored_uppercase(self, warehouses):
        assert (await warehouses.create(creation_payload(code="khi-99"))).code == "KHI-99"

    async def test_zones_and_hours_can_be_created_in_the_same_request(self, warehouses):
        warehouse = await warehouses.create(
            creation_payload(
                zones=[{"code": "A1", "name": "Ambient A1", "capacity_units": 5000}],
                operating_hours=[
                    {"day_of_week": 1, "opens_at": "08:00:00", "closes_at": "20:00:00"},
                    {"day_of_week": 7, "is_closed": True},
                ],
            )
        )
        assert [z.code for z in warehouse.zones] == ["A1"]
        assert len(warehouse.operating_hours) == 2

    async def test_supplying_coordinates_marks_the_facility_geocoded(self, warehouses):
        """Hand-entered coordinates count as freshly resolved."""
        warehouse = await warehouses.create(creation_payload(latitude=24.8607, longitude=67.0011))
        assert warehouse.geocoded_at is not None

    async def test_a_facility_without_coordinates_is_not_geocoded(self, warehouses):
        assert (await warehouses.create(creation_payload())).geocoded_at is None

    async def test_a_duplicate_code_is_refused_by_the_database(self, warehouses, db_session):
        first = creation_payload()
        await warehouses.create(first)
        with pytest.raises(IntegrityError):
            await warehouses.create(creation_payload(code=first.code))
        await db_session.rollback()


class TestReadScoping:
    async def test_an_admin_sees_any_facility(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        found = await warehouses.get(warehouse.id, scope=factory.scope(admin))
        assert found.id == warehouse.id

    async def test_an_operator_sees_a_facility_assigned_to_them(self, warehouses, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        await factory.assign(operator, warehouse)

        scope = factory.scope(operator, warehouse_ids=[warehouse.id])
        assert (await warehouses.get(warehouse.id, scope=scope)).id == warehouse.id

    async def test_an_operator_is_told_an_unassigned_facility_does_not_exist(
        self, warehouses, factory
    ):
        """404, not 403 — existence is not information they should gain."""
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse()
        theirs = await factory.warehouse()

        scope = factory.scope(operator, warehouse_ids=[mine.id])
        with pytest.raises(NotFoundError):
            await warehouses.get(theirs.id, scope=scope)

    async def test_an_operator_with_no_assignments_sees_nothing(self, warehouses, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        with pytest.raises(NotFoundError):
            await warehouses.get(warehouse.id, scope=factory.scope(operator, warehouse_ids=[]))

    async def test_an_unknown_id_is_a_not_found(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        with pytest.raises(NotFoundError):
            await warehouses.get(uuid.uuid4(), scope=factory.scope(admin))


class TestListing:
    async def test_an_operator_only_lists_their_own_facilities(self, warehouses, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse(city="Scopedcity")
        await factory.warehouse(city="Scopedcity")

        records, total = await warehouses.list(
            scope=factory.scope(operator, warehouse_ids=[mine.id]), city="Scopedcity"
        )
        assert total == 1
        assert records[0].id == mine.id

    async def test_an_admin_lists_every_facility_in_the_city(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        await factory.warehouse(city="Adminville")
        await factory.warehouse(city="Adminville")
        _, total = await warehouses.list(scope=factory.scope(admin), city="Adminville")
        assert total == 2

    async def test_a_courier_only_ever_sees_operational_facilities(self, warehouses, factory):
        """Couriers navigate to warehouses; a closed one is not a destination."""
        courier = await factory.user(UserRole.COURIER)
        await factory.warehouse(city="Courierton", status=WarehouseStatus.ACTIVE)
        await factory.warehouse(city="Courierton", status=WarehouseStatus.MAINTENANCE)

        records, total = await warehouses.list(scope=factory.scope(courier), city="Courierton")
        assert total == 1
        assert records[0].status is WarehouseStatus.ACTIVE

    async def test_filters_by_type_and_search(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        hub = await factory.warehouse(type=WarehouseType.DISTRIBUTION_HUB, city="Filtertown")
        await factory.warehouse(type=WarehouseType.RETURNS_CENTER, city="Filtertown")

        by_type, total = await warehouses.list(
            scope=factory.scope(admin), city="Filtertown", type=WarehouseType.DISTRIBUTION_HUB
        )
        assert total == 1 and by_type[0].id == hub.id

        by_code, _ = await warehouses.list(scope=factory.scope(admin), search=hub.code)
        assert by_code[0].id == hub.id

    async def test_soft_deleted_facilities_are_excluded(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse(city="Disappearing")
        scope = factory.scope(admin)

        _, before = await warehouses.list(scope=scope, city="Disappearing")
        await warehouses.delete(warehouse.id, scope=scope)
        _, after = await warehouses.list(scope=scope, city="Disappearing")
        assert (before, after) == (1, 0)


class TestUpdate:
    async def test_applies_only_the_supplied_fields(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse(name="Before", city="Karachi")
        updated = await warehouses.update(
            warehouse.id, WarehouseUpdate(name="After"), scope=factory.scope(admin)
        )
        assert updated.name == "After"
        assert updated.city == "Karachi"

    async def test_moving_the_coordinates_refreshes_the_geocode_timestamp(
        self, warehouses, factory
    ):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        assert warehouse.geocoded_at is None

        updated = await warehouses.update(
            warehouse.id, WarehouseUpdate(latitude=24.9), scope=factory.scope(admin)
        )
        assert updated.geocoded_at is not None

    async def test_a_non_positional_update_leaves_the_geocode_alone(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        updated = await warehouses.update(
            warehouse.id, WarehouseUpdate(name="Renamed"), scope=factory.scope(admin)
        )
        assert updated.geocoded_at is None

    async def test_status_changes_go_through_their_own_path(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        updated = await warehouses.change_status(
            warehouse.id,
            WarehouseStatusUpdate(status=WarehouseStatus.MAINTENANCE, reason="Roof repair"),
            scope=factory.scope(admin),
        )
        assert updated.status is WarehouseStatus.MAINTENANCE


class TestSoftDeletion:
    async def test_the_row_survives_and_the_facility_goes_inactive(
        self, warehouses, factory, db_session
    ):
        """Shipments and stock reference this row; it has to keep resolving."""
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()

        await warehouses.delete(warehouse.id, scope=factory.scope(admin))

        still_there = await db_session.scalar(select(Warehouse).where(Warehouse.id == warehouse.id))
        assert still_there is not None
        assert still_there.deleted_at is not None
        assert still_there.deleted_by == admin.id
        assert still_there.status is WarehouseStatus.INACTIVE

    async def test_a_deleted_facility_is_invisible(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        await warehouses.delete(warehouse.id, scope=factory.scope(admin))
        with pytest.raises(NotFoundError):
            await warehouses.get(warehouse.id, scope=factory.scope(admin))

    async def test_it_can_be_restored(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        await warehouses.delete(warehouse.id, scope=factory.scope(admin))

        restored = await warehouses.restore(warehouse.id, scope=factory.scope(admin))
        assert restored.deleted_at is None
        assert restored.status is WarehouseStatus.ACTIVE


class TestZones:
    async def test_an_admin_can_add_a_zone(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        zone = await warehouses.add_zone(
            warehouse.id,
            WarehouseZoneCreate(code="a1", name="Ambient A1", capacity_units=100),
            scope=factory.scope(admin),
        )
        assert zone.code == "A1"
        assert zone.type is ZoneType.STORAGE

    async def test_an_assigned_operator_can_add_a_zone(self, warehouses, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        await factory.assign(operator, warehouse)

        zone = await warehouses.add_zone(
            warehouse.id,
            WarehouseZoneCreate(code="B1", name="Bulk B1", capacity_units=100),
            scope=factory.scope(operator, warehouse_ids=[warehouse.id]),
        )
        assert zone.warehouse_id == warehouse.id

    async def test_an_unassigned_operator_cannot_add_a_zone(self, warehouses, factory):
        """A write attempt on a known-existing facility is a 403, not a 404."""
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        with pytest.raises(PermissionDeniedError):
            await warehouses.add_zone(
                warehouse.id,
                WarehouseZoneCreate(code="C1", name="Cold C1", capacity_units=100),
                scope=factory.scope(operator, warehouse_ids=[]),
            )

    async def test_zone_codes_are_unique_within_a_facility(self, warehouses, factory, db_session):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        await factory.zone(warehouse, code="D1")

        with pytest.raises(IntegrityError):
            await warehouses.add_zone(
                warehouse.id,
                WarehouseZoneCreate(code="D1", name="Duplicate", capacity_units=100),
                scope=factory.scope(admin),
            )
        await db_session.rollback()

    async def test_the_same_zone_code_is_fine_in_a_different_facility(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        first = await factory.warehouse()
        second = await factory.warehouse()
        await factory.zone(first, code="E1")

        zone = await warehouses.add_zone(
            second.id,
            WarehouseZoneCreate(code="E1", name="Same code elsewhere", capacity_units=100),
            scope=factory.scope(admin),
        )
        assert zone.code == "E1"

    async def test_a_zone_can_be_updated(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        zone = await factory.zone(warehouse, code="F1")

        updated = await warehouses.update_zone(
            zone.id, WarehouseZoneUpdate(capacity_units=999), scope=factory.scope(admin)
        )
        assert updated.capacity_units == 999

    async def test_deleting_a_zone_is_soft(self, warehouses, factory, db_session):
        """Stock positions point at zones; the row must keep resolving."""
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        zone = await factory.zone(warehouse, code="G1")

        await warehouses.delete_zone(zone.id, scope=factory.scope(admin))

        still_there = await db_session.scalar(
            select(WarehouseZone).where(WarehouseZone.id == zone.id)
        )
        assert still_there is not None
        assert still_there.deleted_at is not None
        assert still_there.is_active is False

    async def test_a_deleted_zone_disappears_from_the_facility(self, warehouses, factory):
        """Regression: it used to keep showing up, so the delete looked like a no-op."""
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        kept = await factory.zone(warehouse, code="K1")
        removed = await factory.zone(warehouse, code="K2")

        await warehouses.delete_zone(removed.id, scope=factory.scope(admin))

        detail = await warehouses.get(warehouse.id, scope=factory.scope(admin))
        assert [zone.id for zone in detail.zones] == [kept.id]

    async def test_a_deleted_zone_can_no_longer_be_updated(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        zone = await factory.zone(warehouse, code="H1")
        await warehouses.delete_zone(zone.id, scope=factory.scope(admin))

        with pytest.raises(NotFoundError):
            await warehouses.update_zone(
                zone.id, WarehouseZoneUpdate(name="Ghost"), scope=factory.scope(admin)
            )


class TestOperatingHours:
    async def test_the_schedule_is_returned_in_weekday_order(self, warehouses, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        for day in (3, 1, 2):
            await factory.operating_hours(warehouse, day=day)

        rows = await warehouses.list_hours(warehouse.id, scope=factory.scope(admin))
        assert [r.day_of_week for r in rows] == [1, 2, 3]

    async def test_replacing_the_week_swaps_the_whole_schedule(self, warehouses, factory):
        """PUT semantics: an absent day means closed, not unchanged."""
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        for day in range(1, 6):
            await factory.operating_hours(warehouse, day=day)

        rows = await warehouses.replace_hours(
            warehouse.id,
            WeeklyScheduleUpdate(
                days=[
                    {"day_of_week": 6, "opens_at": "10:00:00", "closes_at": "14:00:00"},
                    {"day_of_week": 7, "is_closed": True},
                ]
            ),
            scope=factory.scope(admin),
        )
        assert [r.day_of_week for r in rows] == [6, 7]

    async def test_an_unassigned_operator_cannot_replace_the_schedule(self, warehouses, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        with pytest.raises(PermissionDeniedError):
            await warehouses.replace_hours(
                warehouse.id,
                WeeklyScheduleUpdate(days=[{"day_of_week": 1, "is_closed": True}]),
                scope=factory.scope(operator, warehouse_ids=[]),
            )

    async def test_reading_hours_respects_row_scoping(self, warehouses, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        with pytest.raises(NotFoundError):
            await warehouses.list_hours(
                warehouse.id, scope=factory.scope(operator, warehouse_ids=[])
            )
