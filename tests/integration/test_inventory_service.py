"""Stock reservation, commitment and release.

This is the consistency-critical path. The invariant that must never break:
``reserved_qty <= on_hand_qty``, and no unit is ever promised to two shipments.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.constants.enums import ReservationStatus, ShipmentStatus, StockMovementType
from app.models.inventory_reservation import InventoryReservation
from app.models.stock_movement import StockMovement
from app.services.inventory_service import InsufficientStockError, InventoryService


@pytest.fixture
def inventory(db_session) -> InventoryService:
    return InventoryService(db_session)


@pytest.fixture
async def stocked(factory):
    """A warehouse with one SKU and 100 units on hand, plus a shipment to hold for."""
    warehouse = await factory.warehouse()
    sku = await factory.sku(code=f"SKU-{uuid.uuid4().hex[:8]}")
    item = await factory.stock(warehouse, sku, on_hand_qty=100)
    shipment = await factory.shipment(warehouse)
    return warehouse, sku, item, shipment


class TestReserve:
    async def test_holding_stock_moves_it_out_of_availability(self, inventory, stocked):
        warehouse, sku, item, shipment = stocked
        reservations = await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 30, sku.code)]
        )
        assert len(reservations) == 1
        assert item.on_hand_qty == 100
        assert item.reserved_qty == 30

    async def test_the_hold_records_what_it_is_for(self, inventory, stocked):
        warehouse, sku, _, shipment = stocked
        [reservation] = await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 5, sku.code)]
        )
        assert reservation.status is ReservationStatus.HELD
        assert reservation.shipment_id == shipment.id
        assert reservation.quantity == 5
        assert reservation.expires_at > datetime.now(UTC)

    async def test_the_hold_key_is_deterministic_so_a_retry_cannot_double_book(
        self, inventory, stocked
    ):
        """Same shipment, same SKU — the unique index refuses a second hold."""
        warehouse, sku, _, shipment = stocked
        [reservation] = await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 5, sku.code)]
        )
        assert reservation.idempotency_key == f"shipment:{shipment.id}:sku:{sku.id}"

    async def test_the_optimistic_version_counter_advances(self, inventory, stocked):
        warehouse, sku, item, shipment = stocked
        before = item.version
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 1, sku.code)]
        )
        assert item.version == before + 1

    async def test_asking_for_more_than_is_available_is_refused(self, inventory, stocked):
        warehouse, sku, item, shipment = stocked
        with pytest.raises(InsufficientStockError, match="Insufficient stock"):
            await inventory.reserve_for_shipment(
                shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 101, sku.code)]
            )
        assert item.reserved_qty == 0

    async def test_the_refusal_says_how_much_there_actually_is(self, inventory, stocked):
        warehouse, sku, _, shipment = stocked
        with pytest.raises(InsufficientStockError) as exc:
            await inventory.reserve_for_shipment(
                shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 150, sku.code)]
            )
        assert "requested 150" in str(exc.value)
        assert "available 100" in str(exc.value)

    async def test_already_held_stock_is_not_available_to_anyone_else(
        self, inventory, stocked, factory
    ):
        warehouse, sku, item, first = stocked
        second = await factory.shipment(warehouse)

        await inventory.reserve_for_shipment(
            shipment_id=first.id, warehouse_id=warehouse.id, lines=[(sku.id, 70, sku.code)]
        )
        with pytest.raises(InsufficientStockError):
            await inventory.reserve_for_shipment(
                shipment_id=second.id, warehouse_id=warehouse.id, lines=[(sku.id, 70, sku.code)]
            )
        assert item.reserved_qty == 70

    async def test_what_is_left_after_a_hold_can_still_be_taken(self, inventory, stocked, factory):
        warehouse, sku, item, first = stocked
        second = await factory.shipment(warehouse)

        await inventory.reserve_for_shipment(
            shipment_id=first.id, warehouse_id=warehouse.id, lines=[(sku.id, 70, sku.code)]
        )
        await inventory.reserve_for_shipment(
            shipment_id=second.id, warehouse_id=warehouse.id, lines=[(sku.id, 30, sku.code)]
        )
        assert item.reserved_qty == 100

    async def test_a_multi_line_hold_is_all_or_nothing(self, inventory, factory):
        """A partially reserved shipment could never be dispatched anyway."""
        warehouse = await factory.warehouse()
        plentiful = await factory.sku()
        scarce = await factory.sku()
        plentiful_stock = await factory.stock(warehouse, plentiful, on_hand_qty=100)
        scarce_stock = await factory.stock(warehouse, scarce, on_hand_qty=1)
        shipment = await factory.shipment(warehouse)

        with pytest.raises(InsufficientStockError):
            await inventory.reserve_for_shipment(
                shipment_id=shipment.id,
                warehouse_id=warehouse.id,
                lines=[(plentiful.id, 10, plentiful.code), (scarce.id, 5, scarce.code)],
            )
        assert plentiful_stock.reserved_qty == 0
        assert scarce_stock.reserved_qty == 0

    async def test_a_sku_with_no_stock_position_here_is_refused_by_name(self, inventory, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()  # never stocked at this facility
        shipment = await factory.shipment(warehouse)

        with pytest.raises(InsufficientStockError, match="No stock position"):
            await inventory.reserve_for_shipment(
                shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 1, sku.code)]
            )

    async def test_stock_at_another_warehouse_does_not_count(self, inventory, factory):
        here = await factory.warehouse()
        elsewhere = await factory.warehouse()
        sku = await factory.sku()
        await factory.stock(elsewhere, sku, on_hand_qty=500)
        shipment = await factory.shipment(here)

        with pytest.raises(InsufficientStockError, match="No stock position"):
            await inventory.reserve_for_shipment(
                shipment_id=shipment.id, warehouse_id=here.id, lines=[(sku.id, 1, sku.code)]
            )


class TestCommit:
    async def test_dispatch_turns_the_hold_into_a_real_reduction(self, inventory, stocked):
        warehouse, sku, item, shipment = stocked
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 40, sku.code)]
        )

        assert await inventory.commit_for_shipment(shipment.id) == 1
        assert item.on_hand_qty == 60
        assert item.reserved_qty == 0

    async def test_the_reservation_is_marked_committed(self, inventory, stocked, db_session):
        warehouse, sku, _, shipment = stocked
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 10, sku.code)]
        )
        await inventory.commit_for_shipment(shipment.id)

        reservation = await db_session.scalar(
            select(InventoryReservation).where(InventoryReservation.shipment_id == shipment.id)
        )
        assert reservation.status is ReservationStatus.COMMITTED
        assert reservation.committed_at is not None

    async def test_a_ledger_entry_records_the_movement(self, inventory, stocked, db_session):
        """Every change of on-hand stock leaves an immutable trail."""
        warehouse, sku, _, shipment = stocked
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 25, sku.code)]
        )
        await inventory.commit_for_shipment(shipment.id)

        movement = await db_session.scalar(
            select(StockMovement).where(StockMovement.reference_id == shipment.id)
        )
        assert movement.type is StockMovementType.OUTBOUND_DISPATCH
        assert movement.quantity_delta == -25
        assert movement.resulting_on_hand == 75
        assert movement.reference_type == "shipment"

    async def test_committing_twice_moves_nothing_the_second_time(self, inventory, stocked):
        """Only HELD reservations are committed, so a repeat is a no-op."""
        warehouse, sku, item, shipment = stocked
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 10, sku.code)]
        )
        await inventory.commit_for_shipment(shipment.id)
        assert await inventory.commit_for_shipment(shipment.id) == 0
        assert item.on_hand_qty == 90

    async def test_committing_a_shipment_with_no_holds_is_harmless(self, inventory, stocked):
        _, _, _, shipment = stocked
        assert await inventory.commit_for_shipment(shipment.id) == 0


class TestRelease:
    async def test_cancelling_gives_the_stock_back(self, inventory, stocked):
        warehouse, sku, item, shipment = stocked
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 40, sku.code)]
        )

        assert await inventory.release_for_shipment(shipment.id, "Customer cancelled") == 1
        assert item.reserved_qty == 0
        assert item.on_hand_qty == 100

    async def test_the_reason_is_recorded(self, inventory, stocked, db_session):
        warehouse, sku, _, shipment = stocked
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 5, sku.code)]
        )
        await inventory.release_for_shipment(shipment.id, "Customer cancelled")

        reservation = await db_session.scalar(
            select(InventoryReservation).where(InventoryReservation.shipment_id == shipment.id)
        )
        assert reservation.status is ReservationStatus.RELEASED
        assert reservation.release_reason == "Customer cancelled"
        assert reservation.released_at is not None

    async def test_released_stock_becomes_available_to_another_shipment(
        self, inventory, stocked, factory
    ):
        warehouse, sku, item, first = stocked
        second = await factory.shipment(warehouse)

        await inventory.reserve_for_shipment(
            shipment_id=first.id, warehouse_id=warehouse.id, lines=[(sku.id, 100, sku.code)]
        )
        await inventory.release_for_shipment(first.id, "Abandoned")
        await inventory.reserve_for_shipment(
            shipment_id=second.id, warehouse_id=warehouse.id, lines=[(sku.id, 100, sku.code)]
        )
        assert item.reserved_qty == 100

    async def test_committed_stock_is_not_released(self, inventory, stocked):
        """Those units have physically left the building."""
        warehouse, sku, item, shipment = stocked
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 20, sku.code)]
        )
        await inventory.commit_for_shipment(shipment.id)

        assert await inventory.release_for_shipment(shipment.id, "Too late") == 0
        assert item.on_hand_qty == 80


class TestExpiry:
    async def test_an_expired_hold_is_released_when_someone_else_wants_the_stock(
        self, inventory, factory
    ):
        """Lazy expiry: no scheduled job is needed for contended stock."""
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=10, reserved_qty=10)
        abandoned = await factory.shipment(warehouse)
        wanted = await factory.shipment(warehouse)
        await factory.reservation(
            item, abandoned.id, quantity=10, expires_at=datetime.now(UTC) - timedelta(minutes=1)
        )

        [reservation] = await inventory.reserve_for_shipment(
            shipment_id=wanted.id, warehouse_id=warehouse.id, lines=[(sku.id, 10, sku.code)]
        )
        assert reservation.quantity == 10
        assert item.reserved_qty == 10  # the new hold, not the old one

    async def test_a_live_hold_is_not_disturbed(self, inventory, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=10, reserved_qty=10)
        live = await factory.shipment(warehouse)
        wanted = await factory.shipment(warehouse)
        await factory.reservation(
            item, live.id, quantity=10, expires_at=datetime.now(UTC) + timedelta(hours=1)
        )

        with pytest.raises(InsufficientStockError):
            await inventory.reserve_for_shipment(
                shipment_id=wanted.id, warehouse_id=warehouse.id, lines=[(sku.id, 10, sku.code)]
            )

    async def test_the_sweep_releases_every_lapsed_hold(self, inventory, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=10, reserved_qty=6)
        for quantity in (2, 4):
            shipment = await factory.shipment(warehouse)
            await factory.reservation(
                item,
                shipment.id,
                quantity=quantity,
                expires_at=datetime.now(UTC) - timedelta(minutes=5),
            )

        assert await inventory.release_expired(warehouse.id) == 2
        assert item.reserved_qty == 0

    async def test_the_sweep_can_be_limited_to_one_warehouse(self, inventory, factory):
        expired_at = datetime.now(UTC) - timedelta(minutes=5)
        first, second = await factory.warehouse(), await factory.warehouse()
        for warehouse in (first, second):
            sku = await factory.sku()
            item = await factory.stock(warehouse, sku, on_hand_qty=5, reserved_qty=5)
            shipment = await factory.shipment(warehouse)
            await factory.reservation(item, shipment.id, quantity=5, expires_at=expired_at)

        assert await inventory.release_expired(first.id) == 1

    async def test_the_sweep_leaves_live_holds_alone(self, inventory, stocked):
        warehouse, sku, _, shipment = stocked
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 5, sku.code)]
        )
        assert await inventory.release_expired(warehouse.id) == 0


class TestGeneratedAvailability:
    async def test_the_database_keeps_available_in_step_with_its_inputs(
        self, inventory, stocked, db_session
    ):
        """``available_qty`` is generated, so it cannot drift from on-hand minus held."""
        warehouse, sku, item, shipment = stocked
        await inventory.reserve_for_shipment(
            shipment_id=shipment.id, warehouse_id=warehouse.id, lines=[(sku.id, 35, sku.code)]
        )
        await db_session.commit()
        await db_session.refresh(item)
        assert item.available_qty == 65

        await inventory.commit_for_shipment(shipment.id)
        await db_session.commit()
        await db_session.refresh(item)
        assert (item.on_hand_qty, item.reserved_qty, item.available_qty) == (65, 0, 65)


class TestReservationsAreScopedToTheirShipment:
    async def test_releasing_one_shipment_does_not_touch_another(self, inventory, stocked, factory):
        warehouse, sku, item, first = stocked
        second = await factory.shipment(warehouse, status=ShipmentStatus.CREATED)
        await inventory.reserve_for_shipment(
            shipment_id=first.id, warehouse_id=warehouse.id, lines=[(sku.id, 20, sku.code)]
        )
        await inventory.reserve_for_shipment(
            shipment_id=second.id, warehouse_id=warehouse.id, lines=[(sku.id, 30, sku.code)]
        )

        await inventory.release_for_shipment(first.id, "Only the first")
        assert item.reserved_qty == 30
