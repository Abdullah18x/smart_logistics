"""Two requests, one unit of stock.

Everything else in the suite runs inside a single rolled-back transaction, which
is exactly the wrong shape for these: row locks and unique-constraint races only
exist between *separate* connections. These tests therefore commit for real and
clean up after themselves.
"""

import asyncio
import uuid
from contextlib import suppress

import pytest
from sqlalchemy import delete, select

from app.models.address import Address
from app.models.idempotency_key import IdempotencyKey
from app.models.inventory_item import InventoryItem
from app.models.inventory_reservation import InventoryReservation
from app.models.package import Package
from app.models.shipment import Shipment
from app.models.shipment_item import ShipmentItem
from app.models.sku import Sku
from app.models.stock_movement import StockMovement
from app.models.user import User
from app.models.warehouse import Warehouse
from app.models.warehouse_zone import WarehouseZone
from app.services.idempotency_service import (
    IdempotencyService,
    RequestInProgressError,
)
from app.services.inventory_service import InsufficientStockError, InventoryService
from tests.factories import Factory

pytestmark = pytest.mark.slow

#: Child-before-parent, so every delete is legal under the RESTRICT rules.
TEARDOWN_ORDER = (
    InventoryReservation,
    StockMovement,
    ShipmentItem,
    Package,
    Shipment,
    Address,
    InventoryItem,
    WarehouseZone,
    Warehouse,
    Sku,
    User,
)


@pytest.fixture
async def contended(committed_session):
    """Ten units of one SKU at one warehouse, committed and visible to every session.

    Yields the ids plus a list of shipment ids competing for that stock.
    """
    async with committed_session() as session:
        factory = Factory(session)
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=10)
        shipments = [(await factory.shipment(warehouse)).id for _ in range(4)]
        await session.commit()
        created = {
            "warehouse_id": warehouse.id,
            "sku_id": sku.id,
            "sku_code": sku.code,
            "item_id": item.id,
            "shipments": shipments,
        }

    try:
        yield created
    finally:
        async with committed_session() as session:
            for model in TEARDOWN_ORDER:
                await session.execute(delete(model))
            await session.execute(delete(IdempotencyKey))
            await session.commit()


async def reserve(committed_session, contended, shipment_id, quantity):
    """One request's worth of work, in its own transaction."""
    async with committed_session() as session:
        try:
            await InventoryService(session).reserve_for_shipment(
                shipment_id=shipment_id,
                warehouse_id=contended["warehouse_id"],
                lines=[(contended["sku_id"], quantity, contended["sku_code"])],
            )
            await session.commit()
            return "accepted"
        except InsufficientStockError:
            await session.rollback()
            return "refused"


class TestCompetingForTheSameStock:
    async def test_only_one_of_two_overlapping_requests_gets_the_stock(
        self, committed_session, contended
    ):
        """Seven units each, ten on the shelf: the second must be refused, not queued."""
        first, second = contended["shipments"][:2]
        outcomes = await asyncio.gather(
            reserve(committed_session, contended, first, 7),
            reserve(committed_session, contended, second, 7),
        )
        assert sorted(outcomes) == ["accepted", "refused"]

    async def test_the_remainder_is_still_usable_by_someone_else(
        self, committed_session, contended
    ):
        first, second, third = contended["shipments"][:3]
        assert await reserve(committed_session, contended, first, 7) == "accepted"
        assert await reserve(committed_session, contended, second, 7) == "refused"
        assert await reserve(committed_session, contended, third, 3) == "accepted"
        assert (
            await reserve(committed_session, contended, contended["shipments"][3], 1) == "refused"
        )

    async def test_stock_is_never_oversold_under_load(self, committed_session, contended):
        """Four simultaneous requests for four units of a ten-unit position."""
        outcomes = await asyncio.gather(
            *(
                reserve(committed_session, contended, shipment_id, 4)
                for shipment_id in contended["shipments"]
            )
        )
        assert outcomes.count("accepted") == 2

        async with committed_session() as session:
            item = await session.get(InventoryItem, contended["item_id"])
            assert item.reserved_qty == 8
            assert item.available_qty == 2
            # The invariant that must hold no matter how the race resolved.
            assert item.reserved_qty <= item.on_hand_qty

    async def test_every_accepted_request_left_exactly_one_hold(self, committed_session, contended):
        await asyncio.gather(
            *(
                reserve(committed_session, contended, shipment_id, 4)
                for shipment_id in contended["shipments"]
            )
        )
        async with committed_session() as session:
            holds = (
                await session.scalars(
                    select(InventoryReservation).where(
                        InventoryReservation.inventory_item_id == contended["item_id"]
                    )
                )
            ).all()
            assert len(holds) == 2
            assert sum(hold.quantity for hold in holds) == 8


class TestCompetingForTheSameIdempotencyKey:
    async def test_only_one_simultaneous_request_may_proceed(self, committed_session, contended):
        """The loser is told the first is still in flight, not allowed to act too."""
        key = f"race-{uuid.uuid4()}"

        async def claim():
            async with committed_session() as session:
                try:
                    return await IdempotencyService(session).begin(
                        key=key, endpoint="POST /api/v1/shipments", payload={"a": 1}, user_id=None
                    )
                except RequestInProgressError:
                    return "in-progress"

        outcomes = await asyncio.gather(claim(), claim())
        assert outcomes.count(None) == 1  # exactly one told to go ahead
        assert outcomes.count("in-progress") == 1

    async def test_the_key_is_recorded_once(self, committed_session, contended):
        key = f"race-{uuid.uuid4()}"

        async def claim():
            async with committed_session() as session:
                with suppress(RequestInProgressError):
                    await IdempotencyService(session).begin(
                        key=key,
                        endpoint="POST /api/v1/shipments",
                        payload={"a": 1},
                        user_id=None,
                    )

        await asyncio.gather(*(claim() for _ in range(4)))
        async with committed_session() as session:
            rows = (
                await session.scalars(select(IdempotencyKey).where(IdempotencyKey.key == key))
            ).all()
            assert len(rows) == 1


class TestFailedWritesLeaveNothingBehind:
    async def test_a_shipment_refused_for_lack_of_stock_is_not_persisted(
        self, committed_session, contended
    ):
        """The shipment, its address and its lines are all created before the hold
        is attempted, so only the transaction stops them becoming real."""
        from app.core.access import AccessScope
        from app.core.enums import UserRole
        from app.schemas.shipment import ShipmentCreate
        from app.services.shipment_service import ShipmentService

        async with committed_session() as session:
            admin = await Factory(session).user(UserRole.ADMIN)
            await session.commit()
            actor_id = admin.id

        async with committed_session() as session:
            before = await session.scalar(
                select(Shipment.id).where(Shipment.origin_warehouse_id == contended["warehouse_id"])
            )
            assert before is not None  # the fixture's own shipments exist
            existing = len(
                (
                    await session.scalars(
                        select(Shipment).where(
                            Shipment.origin_warehouse_id == contended["warehouse_id"]
                        )
                    )
                ).all()
            )

        async with committed_session() as session:
            with pytest.raises(InsufficientStockError):
                await ShipmentService(session).create(
                    ShipmentCreate(
                        origin_warehouse_id=contended["warehouse_id"],
                        destination_address={
                            "contact_name": "Ayesha Khan",
                            "contact_phone": "+923001234567",
                            "line1": "House 12",
                            "city": "Karachi",
                        },
                        items=[{"sku_id": contended["sku_id"], "quantity": 999}],
                    ),
                    scope=AccessScope(role=UserRole.ADMIN, user_id=actor_id),
                )
            await session.rollback()

        async with committed_session() as session:
            after = len(
                (
                    await session.scalars(
                        select(Shipment).where(
                            Shipment.origin_warehouse_id == contended["warehouse_id"]
                        )
                    )
                ).all()
            )
            assert after == existing
