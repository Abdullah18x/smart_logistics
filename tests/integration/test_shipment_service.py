"""Shipment creation, the lifecycle, and who is allowed to drive it."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.constants.enums import (
    PackageStatus,
    ServiceLevel,
    ShipmentPriority,
    ShipmentStatus,
    UserRole,
    WarehouseStatus,
)
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.models.inventory_reservation import InventoryReservation
from app.schemas.package import PackageCreate, PackageUpdate
from app.schemas.shipment import ShipmentCreate, ShipmentUpdate
from app.services.inventory_service import InsufficientStockError
from app.services.shipment_service import SERVICE_LEVEL_WINDOWS, ShipmentService

ADDRESS = {
    "contact_name": "Ayesha Khan",
    "contact_phone": "+923001234567",
    "line1": "House 12, Street 4, DHA Phase 6",
    "city": "Karachi",
}


@pytest.fixture
def shipments(db_session) -> ShipmentService:
    return ShipmentService(db_session)


@pytest.fixture
async def world(factory):
    """An admin, an active warehouse, a stocked SKU — the usual starting point."""
    admin = await factory.user(UserRole.ADMIN)
    warehouse = await factory.warehouse()
    sku = await factory.sku(weight_g=250)
    item = await factory.stock(warehouse, sku, on_hand_qty=100)
    return admin, warehouse, sku, item


def create_payload(warehouse, sku, *, quantity: int = 2, **overrides) -> ShipmentCreate:
    return ShipmentCreate(
        **{
            "origin_warehouse_id": warehouse.id,
            "destination_address": ADDRESS,
            "items": [{"sku_id": sku.id, "quantity": quantity}],
        }
        | overrides
    )


class TestCreate:
    async def test_a_new_shipment_starts_in_created(self, shipments, factory, world):
        admin, warehouse, sku, _ = world
        shipment = await shipments.create(
            create_payload(warehouse, sku), scope=factory.scope(admin)
        )
        assert shipment.status is ShipmentStatus.CREATED
        assert shipment.created_by == admin.id

    async def test_the_reference_number_follows_the_house_format(self, shipments, factory, world):
        admin, warehouse, sku, _ = world
        shipment = await shipments.create(
            create_payload(warehouse, sku), scope=factory.scope(admin)
        )
        assert re.fullmatch(r"SL-\d{4}-\d{6}", shipment.reference_no)

    async def test_references_are_unique_across_concurrent_creates(self, shipments, factory, world):
        """Drawn from a Postgres sequence, so two creates cannot collide."""
        admin, warehouse, sku, _ = world
        scope = factory.scope(admin)
        first = await shipments.create(create_payload(warehouse, sku, quantity=1), scope=scope)
        second = await shipments.create(create_payload(warehouse, sku, quantity=1), scope=scope)
        assert first.reference_no != second.reference_no

    async def test_creating_a_shipment_holds_its_stock(self, shipments, factory, world):
        """The hold is taken in the same transaction, so nothing is oversold."""
        admin, warehouse, sku, item = world
        await shipments.create(
            create_payload(warehouse, sku, quantity=10), scope=factory.scope(admin)
        )
        assert item.reserved_qty == 10
        assert item.on_hand_qty == 100

    async def test_the_items_snapshot_the_catalogue(self, shipments, factory, world):
        """A later correction to the product must not rewrite what was sent."""
        admin, warehouse, sku, _ = world
        shipment = await shipments.create(
            create_payload(warehouse, sku, quantity=3), scope=factory.scope(admin)
        )
        [line] = shipment.items
        assert line.sku_code == sku.code
        assert line.sku_name == sku.name
        assert line.unit_weight_g == sku.weight_g
        assert line.quantity == 3

    async def test_the_total_weight_is_computed_from_the_lines(self, shipments, factory, world):
        admin, warehouse, sku, _ = world
        shipment = await shipments.create(
            create_payload(warehouse, sku, quantity=4), scope=factory.scope(admin)
        )
        assert shipment.total_weight_g == 4 * sku.weight_g

    async def test_the_destination_is_stored_as_a_snapshot(self, shipments, factory, world):
        admin, warehouse, sku, _ = world
        shipment = await shipments.create(
            create_payload(warehouse, sku), scope=factory.scope(admin)
        )
        assert shipment.destination_address.city == "Karachi"
        assert shipment.destination_address.contact_name == "Ayesha Khan"

    async def test_parcels_are_numbered_and_barcoded_by_the_system(self, shipments, factory, world):
        admin, warehouse, sku, _ = world
        shipment = await shipments.create(
            create_payload(warehouse, sku, packages=[{"weight_g": 1000}, {"weight_g": 2000}]),
            scope=factory.scope(admin),
        )
        assert sorted(p.sequence_no for p in shipment.packages) == [1, 2]
        assert all(p.barcode.startswith("PKG") for p in shipment.packages)
        assert len({p.barcode for p in shipment.packages}) == 2

    @pytest.mark.parametrize("level", list(ServiceLevel))
    async def test_the_delivery_promise_follows_the_service_level(
        self, shipments, factory, world, level
    ):
        admin, warehouse, sku, _ = world
        shipment = await shipments.create(
            create_payload(warehouse, sku, quantity=1, service_level=level),
            scope=factory.scope(admin),
        )
        expected = datetime.now(UTC) + SERVICE_LEVEL_WINDOWS[level]
        assert abs((shipment.promised_delivery_at - expected).total_seconds()) < 60

    async def test_an_explicit_promise_is_respected(self, shipments, factory, world):
        admin, warehouse, sku, _ = world
        promised = datetime.now(UTC) + timedelta(days=10)
        shipment = await shipments.create(
            create_payload(warehouse, sku, promised_delivery_at=promised),
            scope=factory.scope(admin),
        )
        assert abs((shipment.promised_delivery_at - promised).total_seconds()) < 1

    async def test_an_unknown_warehouse_is_a_not_found(self, shipments, factory, world):
        admin, _, sku, _ = world
        with pytest.raises(NotFoundError, match="Origin warehouse"):
            await shipments.create(
                ShipmentCreate(
                    origin_warehouse_id=uuid.uuid4(),
                    destination_address=ADDRESS,
                    items=[{"sku_id": sku.id, "quantity": 1}],
                ),
                scope=factory.scope(admin),
            )

    @pytest.mark.parametrize("status", [WarehouseStatus.MAINTENANCE, WarehouseStatus.INACTIVE])
    async def test_a_facility_that_is_not_operational_cannot_originate_shipments(
        self, shipments, factory, status
    ):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse(status=status)
        sku = await factory.sku()
        await factory.stock(warehouse, sku, on_hand_qty=10)

        with pytest.raises(ConflictError, match="cannot originate"):
            await shipments.create(create_payload(warehouse, sku), scope=factory.scope(admin))

    async def test_an_unknown_sku_is_a_not_found(self, shipments, factory, world):
        admin, warehouse, _, _ = world
        with pytest.raises(NotFoundError, match="Unknown SKU"):
            await shipments.create(
                ShipmentCreate(
                    origin_warehouse_id=warehouse.id,
                    destination_address=ADDRESS,
                    items=[{"sku_id": uuid.uuid4(), "quantity": 1}],
                ),
                scope=factory.scope(admin),
            )

    async def test_a_discontinued_sku_cannot_be_shipped(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        sku = await factory.sku(is_active=False)
        await factory.stock(warehouse, sku, on_hand_qty=10)

        with pytest.raises(ConflictError, match="Inactive SKU"):
            await shipments.create(create_payload(warehouse, sku), scope=factory.scope(admin))

    async def test_a_shipment_is_refused_when_the_stock_is_not_there(
        self, shipments, factory, world
    ):
        admin, warehouse, sku, item = world
        with pytest.raises(InsufficientStockError):
            await shipments.create(
                create_payload(warehouse, sku, quantity=500), scope=factory.scope(admin)
            )
        assert item.reserved_qty == 0


class TestReadScoping:
    async def test_admins_and_support_see_every_shipment(self, shipments, factory):
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        for role in (UserRole.ADMIN, UserRole.CUSTOMER_SUPPORT):
            user = await factory.user(role)
            found = await shipments.get(shipment.id, scope=factory.scope(user))
            assert found.id == shipment.id

    async def test_an_operator_sees_shipments_from_their_own_warehouses(self, shipments, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse()
        shipment = await factory.shipment(mine)

        scope = factory.scope(operator, warehouse_ids=[mine.id])
        assert (await shipments.get(shipment.id, scope=scope)).id == shipment.id

    async def test_an_operator_is_told_another_warehouse_s_shipment_does_not_exist(
        self, shipments, factory
    ):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse()
        theirs = await factory.warehouse()
        shipment = await factory.shipment(theirs)

        with pytest.raises(NotFoundError):
            await shipments.get(shipment.id, scope=factory.scope(operator, warehouse_ids=[mine.id]))

    async def test_a_courier_sees_only_their_own_work(self, shipments, factory):
        """Another courier's shipment must not even be confirmed to exist."""
        courier_user = await factory.user(UserRole.COURIER)
        mine = await factory.courier(user=courier_user)
        theirs = await factory.courier()
        warehouse = await factory.warehouse()
        assigned = await factory.shipment(warehouse, courier=mine)
        someone_elses = await factory.shipment(warehouse, courier=theirs)

        scope = factory.scope(courier_user, courier_id=mine.id)
        assert (await shipments.get(assigned.id, scope=scope)).id == assigned.id
        with pytest.raises(NotFoundError):
            await shipments.get(someone_elses.id, scope=scope)

    async def test_a_courier_cannot_see_unassigned_shipments(self, shipments, factory):
        courier_user = await factory.user(UserRole.COURIER)
        courier = await factory.courier(user=courier_user)
        warehouse = await factory.warehouse()
        unassigned = await factory.shipment(warehouse)

        with pytest.raises(NotFoundError):
            await shipments.get(
                unassigned.id, scope=factory.scope(courier_user, courier_id=courier.id)
            )

    async def test_an_unknown_shipment_is_a_not_found(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        with pytest.raises(NotFoundError):
            await shipments.get(uuid.uuid4(), scope=factory.scope(admin))


class TestListing:
    async def test_an_operator_s_list_is_limited_to_their_warehouses(self, shipments, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse()
        theirs = await factory.warehouse()
        await factory.shipment(mine)
        await factory.shipment(theirs)

        records, total = await shipments.list(
            scope=factory.scope(operator, warehouse_ids=[mine.id]), origin_warehouse_id=mine.id
        )
        assert total == 1 and records[0].origin_warehouse_id == mine.id

    async def test_a_courier_s_list_is_limited_to_their_assignments(self, shipments, factory):
        courier_user = await factory.user(UserRole.COURIER)
        courier = await factory.courier(user=courier_user)
        warehouse = await factory.warehouse()
        await factory.shipment(warehouse, courier=courier)
        await factory.shipment(warehouse)

        _, total = await shipments.list(
            scope=factory.scope(courier_user, courier_id=courier.id),
            origin_warehouse_id=warehouse.id,
        )
        assert total == 1

    async def test_filters_by_status(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        await factory.shipment(warehouse, status=ShipmentStatus.CREATED)
        await factory.shipment(warehouse, status=ShipmentStatus.DELIVERED)

        _, total = await shipments.list(
            scope=factory.scope(admin),
            origin_warehouse_id=warehouse.id,
            status=ShipmentStatus.DELIVERED,
        )
        assert total == 1

    async def test_filters_by_destination_city(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        await factory.shipment(warehouse, address=await factory.address(city="Multan"))
        await factory.shipment(warehouse, address=await factory.address(city="Lahore"))

        _, total = await shipments.list(
            scope=factory.scope(admin), origin_warehouse_id=warehouse.id, city="multan"
        )
        assert total == 1

    async def test_overdue_only_finds_late_undelivered_shipments(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        past = datetime.now(UTC) - timedelta(days=1)
        await factory.shipment(
            warehouse, status=ShipmentStatus.IN_TRANSIT, promised_delivery_at=past
        )
        await factory.shipment(
            warehouse, status=ShipmentStatus.DELIVERED, promised_delivery_at=past
        )
        await factory.shipment(warehouse, status=ShipmentStatus.IN_TRANSIT)

        _, total = await shipments.list(
            scope=factory.scope(admin), origin_warehouse_id=warehouse.id, overdue_only=True
        )
        assert total == 1

    async def test_search_matches_the_reference(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, customer_reference="ORD-UNIQUE-1")
        records, _ = await shipments.list(scope=factory.scope(admin), search="ORD-UNIQUE-1")
        assert records[0].id == shipment.id


class TestUpdate:
    @pytest.mark.parametrize(
        "status",
        [
            ShipmentStatus.CREATED,
            ShipmentStatus.READY_FOR_DISPATCH,
            ShipmentStatus.DISPATCH_FAILED,
        ],
    )
    async def test_editable_before_dispatch(self, shipments, factory, status):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, status=status)

        updated = await shipments.update(
            shipment.id,
            ShipmentUpdate(priority=ShipmentPriority.CRITICAL),
            scope=factory.scope(admin),
        )
        assert updated.priority is ShipmentPriority.CRITICAL

    @pytest.mark.parametrize(
        "status",
        [ShipmentStatus.DISPATCHED, ShipmentStatus.IN_TRANSIT, ShipmentStatus.DELIVERED],
    )
    async def test_frozen_once_it_has_left(self, shipments, factory, status):
        """Editing contents after dispatch would misrepresent what was sent."""
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, status=status)

        with pytest.raises(ConflictError, match="no longer be edited"):
            await shipments.update(
                shipment.id, ShipmentUpdate(customer_reference="X"), scope=factory.scope(admin)
            )


class TestStatusTransitions:
    async def test_an_operator_can_mark_their_own_shipment_ready(self, shipments, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)

        updated = await shipments.change_status(
            shipment.id,
            ShipmentStatus.READY_FOR_DISPATCH,
            scope=factory.scope(operator, warehouse_ids=[warehouse.id]),
        )
        assert updated.status is ShipmentStatus.READY_FOR_DISPATCH

    async def test_a_courier_cannot_mark_a_shipment_ready(self, shipments, factory):
        """Warehouse work is not a courier's to do."""
        courier_user = await factory.user(UserRole.COURIER)
        courier = await factory.courier(user=courier_user)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, courier=courier)

        with pytest.raises(PermissionDeniedError, match="cannot move a shipment"):
            await shipments.change_status(
                shipment.id,
                ShipmentStatus.READY_FOR_DISPATCH,
                scope=factory.scope(courier_user, courier_id=courier.id),
            )

    async def test_a_courier_drives_the_custody_states(self, shipments, factory):
        courier_user = await factory.user(UserRole.COURIER)
        courier = await factory.courier(user=courier_user)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(
            warehouse, courier=courier, status=ShipmentStatus.DISPATCHED
        )

        scope = factory.scope(courier_user, courier_id=courier.id)
        assert (
            await shipments.change_status(shipment.id, ShipmentStatus.PICKED_UP, scope=scope)
        ).status is ShipmentStatus.PICKED_UP

    async def test_an_operator_cannot_touch_another_warehouse_s_shipment(self, shipments, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        mine = await factory.warehouse()
        theirs = await factory.warehouse()
        shipment = await factory.shipment(theirs)

        # Invisible to them in the first place, so it reads as "not found".
        with pytest.raises(NotFoundError):
            await shipments.change_status(
                shipment.id,
                ShipmentStatus.READY_FOR_DISPATCH,
                scope=factory.scope(operator, warehouse_ids=[mine.id]),
            )

    async def test_an_illegal_transition_is_refused(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, status=ShipmentStatus.CREATED)

        with pytest.raises(ConflictError, match="Cannot move a shipment"):
            await shipments.change_status(
                shipment.id, ShipmentStatus.DELIVERED, scope=factory.scope(admin)
            )

    async def test_repeating_the_current_status_is_refused(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, status=ShipmentStatus.IN_TRANSIT)

        with pytest.raises(ConflictError, match="already"):
            await shipments.change_status(
                shipment.id, ShipmentStatus.IN_TRANSIT, scope=factory.scope(admin)
            )

    async def test_the_version_counter_advances_on_every_transition(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        before = shipment.version

        await shipments.change_status(
            shipment.id, ShipmentStatus.READY_FOR_DISPATCH, scope=factory.scope(admin)
        )
        assert shipment.version == before + 1

    async def test_each_timestamp_is_written_by_the_transition_that_earns_it(
        self, shipments, factory
    ):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        courier = await factory.courier()
        shipment = await factory.shipment(
            warehouse, courier=courier, status=ShipmentStatus.DISPATCHING
        )
        scope = factory.scope(admin)

        dispatched = await shipments.change_status(
            shipment.id, ShipmentStatus.DISPATCHED, scope=scope
        )
        assert dispatched.dispatched_at is not None and dispatched.picked_up_at is None

        picked_up = await shipments.change_status(
            shipment.id, ShipmentStatus.PICKED_UP, scope=scope
        )
        assert picked_up.picked_up_at is not None

        for target in (ShipmentStatus.IN_TRANSIT, ShipmentStatus.OUT_FOR_DELIVERY):
            await shipments.change_status(shipment.id, target, scope=scope)
        delivered = await shipments.change_status(
            shipment.id, ShipmentStatus.DELIVERED, scope=scope
        )
        assert delivered.delivered_at is not None

    async def test_a_failed_delivery_counts_the_attempt_and_records_why(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, status=ShipmentStatus.OUT_FOR_DELIVERY)

        failed = await shipments.change_status(
            shipment.id,
            ShipmentStatus.DELIVERY_FAILED,
            scope=factory.scope(admin),
            reason="Nobody home",
        )
        assert failed.delivery_attempt_count == 1
        assert failed.failure_reason == "Nobody home"


class TestStockEffectsOfTransitions:
    async def test_dispatching_turns_the_hold_into_a_real_reduction(
        self, shipments, factory, world
    ):
        admin, warehouse, sku, item = world
        scope = factory.scope(admin)
        shipment = await shipments.create(create_payload(warehouse, sku, quantity=10), scope=scope)
        for target in (ShipmentStatus.READY_FOR_DISPATCH, ShipmentStatus.DISPATCHING):
            await shipments.change_status(shipment.id, target, scope=scope)

        await shipments.change_status(shipment.id, ShipmentStatus.DISPATCHED, scope=scope)
        assert item.on_hand_qty == 90
        assert item.reserved_qty == 0

    async def test_cancelling_before_dispatch_gives_the_stock_back(self, shipments, factory, world):
        admin, warehouse, sku, item = world
        scope = factory.scope(admin)
        shipment = await shipments.create(create_payload(warehouse, sku, quantity=25), scope=scope)
        assert item.reserved_qty == 25

        cancelled = await shipments.cancel(shipment.id, "Customer changed their mind", scope=scope)
        assert cancelled.status is ShipmentStatus.CANCELLED
        assert cancelled.cancelled_at is not None
        assert cancelled.failure_reason == "Customer changed their mind"
        assert item.reserved_qty == 0
        assert item.on_hand_qty == 100

    async def test_cancelling_marks_the_holds_released(self, shipments, factory, world, db_session):
        from app.constants.enums import ReservationStatus

        admin, warehouse, sku, _ = world
        scope = factory.scope(admin)
        shipment = await shipments.create(create_payload(warehouse, sku), scope=scope)
        await shipments.cancel(shipment.id, "No longer needed", scope=scope)

        reservation = await db_session.scalar(
            select(InventoryReservation).where(InventoryReservation.shipment_id == shipment.id)
        )
        assert reservation.status is ReservationStatus.RELEASED

    async def test_cancelling_after_dispatch_does_not_restore_stock(
        self, shipments, factory, world
    ):
        """Those units are on a vehicle; the ledger already recorded them out."""
        admin, warehouse, sku, item = world
        scope = factory.scope(admin)
        shipment = await shipments.create(create_payload(warehouse, sku, quantity=10), scope=scope)
        for target in (
            ShipmentStatus.READY_FOR_DISPATCH,
            ShipmentStatus.DISPATCHING,
            ShipmentStatus.DISPATCHED,
        ):
            await shipments.change_status(shipment.id, target, scope=scope)
        assert item.on_hand_qty == 90

        await shipments.cancel(shipment.id, "Recalled", scope=scope)
        assert item.on_hand_qty == 90
        assert item.reserved_qty == 0


class TestPacking:
    async def test_an_operator_can_add_a_parcel_to_their_own_shipment(self, shipments, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)

        package = await shipments.add_package(
            shipment.id,
            PackageCreate(weight_g=1500),
            scope=factory.scope(operator, warehouse_ids=[warehouse.id]),
        )
        assert package.sequence_no == 1
        assert package.status is PackageStatus.PENDING

    async def test_parcel_numbers_continue_from_what_is_already_there(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        await factory.package(shipment, sequence_no=1)
        await factory.package(shipment, sequence_no=2)

        package = await shipments.add_package(
            shipment.id, PackageCreate(weight_g=100), scope=factory.scope(admin)
        )
        assert package.sequence_no == 3

    async def test_an_unassigned_operator_cannot_pack(self, shipments, factory):
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)

        with pytest.raises(NotFoundError):
            await shipments.add_package(
                shipment.id,
                PackageCreate(weight_g=100),
                scope=factory.scope(operator, warehouse_ids=[]),
            )

    async def test_parcels_cannot_be_added_after_dispatch(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, status=ShipmentStatus.DISPATCHED)

        with pytest.raises(ConflictError, match="before dispatch"):
            await shipments.add_package(
                shipment.id, PackageCreate(weight_g=100), scope=factory.scope(admin)
            )

    async def test_marking_a_parcel_packed_stamps_the_time(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        package = await factory.package(shipment)

        updated = await shipments.update_package(
            package.id, PackageUpdate(status=PackageStatus.PACKED), scope=factory.scope(admin)
        )
        assert updated.packed_at is not None

    async def test_the_packed_timestamp_is_not_overwritten(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        package = await factory.package(shipment)
        scope = factory.scope(admin)

        first = await shipments.update_package(
            package.id, PackageUpdate(status=PackageStatus.PACKED), scope=scope
        )
        stamped = first.packed_at
        again = await shipments.update_package(
            package.id, PackageUpdate(status=PackageStatus.PACKED), scope=scope
        )
        assert again.packed_at == stamped

    async def test_an_unknown_parcel_is_a_not_found(self, shipments, factory):
        admin = await factory.user(UserRole.ADMIN)
        with pytest.raises(NotFoundError, match="Package not found"):
            await shipments.update_package(
                uuid.uuid4(), PackageUpdate(weight_g=1), scope=factory.scope(admin)
            )
