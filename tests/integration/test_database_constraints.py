"""The database is the authority on integrity — so these test the database.

The project's position (ADR-016 and the amendment to ADR-008) is that
referential integrity, uniqueness and range checks belong in Postgres, not in
Python where a check-then-act is a race. That only holds if the constraints are
really there and really fire.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db_errors import CONSTRAINT_MESSAGES, translate
from app.models.base import (
    CATALOG_SCHEMA,
    COURIER_SCHEMA,
    IDENTITY_SCHEMA,
    INVENTORY_SCHEMA,
    PLATFORM_SCHEMA,
    SHIPMENT_SCHEMA,
    WAREHOUSE_SCHEMA,
)

SCHEMAS = (
    IDENTITY_SCHEMA,
    WAREHOUSE_SCHEMA,
    CATALOG_SCHEMA,
    INVENTORY_SCHEMA,
    SHIPMENT_SCHEMA,
    COURIER_SCHEMA,
    PLATFORM_SCHEMA,
)

#: Tables that carry ``deleted_at``. The others are either immutable ledgers, or
#: rows whose parent's soft delete already hides them (documented in phase_1.md).
SOFT_DELETABLE = {
    "users",
    "warehouses",
    "warehouse_zones",
    "skus",
    "shipments",
    "couriers",
}


class TestSchemaLayout:
    async def test_every_module_schema_exists(self, db_session):
        rows = await db_session.scalars(
            text("SELECT nspname FROM pg_namespace WHERE nspname = ANY(:names)").bindparams(
                names=list(SCHEMAS)
            )
        )
        assert set(rows.all()) == set(SCHEMAS)

    async def test_the_expected_tables_are_present(self, db_session):
        rows = await db_session.scalars(
            text(
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema = ANY(:names)"
            ).bindparams(names=list(SCHEMAS))
        )
        tables = set(rows.all())
        assert len(tables) == 18
        assert "identity.users" in tables
        assert "shipments.shipments" in tables
        assert "platform.idempotency_keys" in tables

    async def test_the_shipment_reference_sequence_exists(self, db_session):
        """Reference numbers come from it, so concurrent creates cannot collide."""
        value = await db_session.scalar(text("SELECT nextval('shipments.shipment_reference_seq')"))
        assert value > 0

    async def test_soft_delete_columns_are_where_they_are_meant_to_be(self, db_session):
        rows = await db_session.scalars(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = ANY(:names) AND column_name = 'deleted_at'"
            ).bindparams(names=list(SCHEMAS))
        )
        assert set(rows.all()) == SOFT_DELETABLE

    async def test_partial_indexes_keep_the_live_data_indexed_separately(self, db_session):
        """They stay the size of the active data no matter how much history piles up."""
        rows = await db_session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = ANY(:names) AND indexdef ILIKE '%WHERE (deleted_at IS NULL)%'"
            ).bindparams(names=list(SCHEMAS))
        )
        found = set(rows.all())
        assert {
            "ix_users_active_role",
            "ix_warehouses_active_city",
            "ix_skus_active_category",
            "ix_shipments_active_status",
            "ix_couriers_active_city",
        } <= found


class TestConstraintCatalogueIsInStepWithTheDatabase:
    async def test_every_mapped_constraint_name_really_exists(self, db_session):
        """A renamed constraint would silently downgrade its message to the generic one.

        Names are stable only because ``models.base`` fixes a naming convention;
        this is what proves the mapping still lines up with reality.
        """
        constraints = set(
            (
                await db_session.scalars(
                    text(
                        "SELECT conname FROM pg_constraint c "
                        "JOIN pg_namespace n ON n.oid = c.connamespace "
                        "WHERE n.nspname = ANY(:names)"
                    ).bindparams(names=list(SCHEMAS))
                )
            ).all()
        )
        indexes = set(
            (
                await db_session.scalars(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = ANY(:names)"
                    ).bindparams(names=list(SCHEMAS))
                )
            ).all()
        )
        known = constraints | indexes
        missing = sorted(name for name in CONSTRAINT_MESSAGES if name not in known)
        assert not missing, f"CONSTRAINT_MESSAGES refers to names not in the database: {missing}"


class TestStockInvariants:
    async def test_reserving_more_than_is_on_hand_is_impossible(self, db_session, factory):
        """The invariant that makes overselling impossible below the application."""
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=10)

        item.reserved_qty = 11
        with pytest.raises(IntegrityError) as exc:
            await db_session.flush()
        assert translate(exc.value).message == "Cannot reserve more stock than is on hand."
        await db_session.rollback()

    async def test_stock_cannot_go_negative(self, db_session, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=5)

        item.on_hand_qty = -1
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_available_quantity_is_computed_by_the_database(self, db_session, factory):
        """Generated, so it can never disagree with its inputs."""
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, on_hand_qty=40, reserved_qty=15)
        assert item.available_qty == 25

        item.reserved_qty = 30
        await db_session.commit()
        await db_session.refresh(item)
        assert item.available_qty == 10

    async def test_a_sku_has_one_stock_position_per_warehouse(self, db_session, factory):
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        await factory.stock(warehouse, sku)

        with pytest.raises(IntegrityError) as exc:
            await factory.stock(warehouse, sku)
        assert "already exists" in translate(exc.value).message
        await db_session.rollback()


class TestReferentialIntegrity:
    async def test_stock_cannot_reference_a_warehouse_that_does_not_exist(
        self, db_session, factory
    ):
        import uuid

        from app.models.inventory_item import InventoryItem

        sku = await factory.sku()
        db_session.add(InventoryItem(warehouse_id=uuid.uuid4(), sku_id=sku.id, on_hand_qty=1))
        with pytest.raises(IntegrityError) as exc:
            await db_session.flush()
        assert translate(exc.value).code == "invalid_reference"
        await db_session.rollback()

    async def test_a_warehouse_with_shipments_cannot_be_hard_deleted(self, db_session, factory):
        """RESTRICT is what forces the codebase to soft-delete instead."""
        warehouse = await factory.warehouse()
        await factory.shipment(warehouse)
        await db_session.commit()

        await db_session.delete(warehouse)
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_deleting_a_user_leaves_their_shipments_standing(self, db_session, factory):
        """SET NULL on ``created_by``: the shipment outlives the account that made it."""
        from app.models.shipment import Shipment

        author = await factory.user()
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, created_by=author)
        await db_session.commit()

        await db_session.delete(author)
        await db_session.commit()

        surviving = await db_session.scalar(
            text("SELECT created_by FROM shipments.shipments WHERE id = :id").bindparams(
                id=shipment.id
            )
        )
        assert surviving is None
        assert await db_session.get(Shipment, shipment.id) is not None

    async def test_clearing_a_zone_does_not_destroy_the_stock_in_it(self, db_session, factory):
        """SET NULL: losing the shelf label must not lose the stock."""
        warehouse = await factory.warehouse()
        zone = await factory.zone(warehouse)
        sku = await factory.sku()
        item = await factory.stock(warehouse, sku, zone_id=zone.id)
        await db_session.commit()

        await db_session.delete(zone)
        await db_session.commit()
        await db_session.refresh(item)
        assert item.zone_id is None
        assert item.on_hand_qty == 100


class TestUniqueness:
    async def test_warehouse_codes_are_unique(self, db_session, factory):
        first = await factory.warehouse()
        with pytest.raises(IntegrityError) as exc:
            await factory.warehouse(code=first.code)
        assert translate(exc.value).message == "A warehouse with this code already exists."
        await db_session.rollback()

    async def test_sku_codes_are_unique(self, db_session, factory):
        first = await factory.sku()
        with pytest.raises(IntegrityError):
            await factory.sku(code=first.code)
        await db_session.rollback()

    async def test_barcodes_are_unique(self, db_session, factory):
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        first = await factory.package(shipment, sequence_no=1)

        with pytest.raises(IntegrityError):
            await factory.package(shipment, sequence_no=2, barcode=first.barcode)
        await db_session.rollback()

    async def test_a_parcel_sequence_is_used_once_per_shipment(self, db_session, factory):
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        await factory.package(shipment, sequence_no=1)

        with pytest.raises(IntegrityError):
            await factory.package(shipment, sequence_no=1)
        await db_session.rollback()

    async def test_an_idempotency_key_is_claimed_once(self, db_session, factory):
        from datetime import UTC, datetime, timedelta

        from app.models.idempotency_key import IdempotencyKey

        for _ in range(2):
            db_session.add(
                IdempotencyKey(
                    key="a-contested-key",
                    endpoint="POST /x",
                    request_hash="deadbeef",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()


class TestOperatingHoursWindow:
    async def test_a_day_cannot_close_before_it_opens(self, db_session, factory):
        """Mirrored in the schema, but the database is the one that enforces it."""
        from datetime import time

        warehouse = await factory.warehouse()
        row = await factory.operating_hours(warehouse, opens_at=time(8), closes_at=time(20))
        row.closes_at = time(6)

        with pytest.raises(IntegrityError) as exc:
            await db_session.flush()
        assert "closing time after its opening time" in translate(exc.value).message
        await db_session.rollback()

    async def test_a_weekday_appears_once_per_warehouse(self, db_session, factory):
        warehouse = await factory.warehouse()
        await factory.operating_hours(warehouse, day=1)
        with pytest.raises(IntegrityError):
            await factory.operating_hours(warehouse, day=1)
        await db_session.rollback()
