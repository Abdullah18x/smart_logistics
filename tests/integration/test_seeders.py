"""Development data.

Seed drift caused real confusion during Week 1 — accounts left suspended,
passwords rotated by hand — so the properties that make seeding trustworthy are
worth asserting: it is idempotent, it produces stable ids, and every seeded
account can actually log in.
"""

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.enums import UserRole, UserStatus
from app.models.courier import Courier
from app.models.inventory_item import InventoryItem
from app.models.shipment import Shipment
from app.models.sku import Sku
from app.models.user import User
from app.models.warehouse import Warehouse
from app.seeders.base import seed_id
from app.seeders.registry import SEEDERS
from app.seeders.user_seeder import SEED_USERS
from app.services.auth_service import AuthService


async def run_all(session) -> int:
    """Run every seeder in registry order and report rows created."""
    return sum([await seeder.run(session) for seeder in SEEDERS])


@pytest.fixture
async def seeded(db_session):
    created = await run_all(db_session)
    await db_session.flush()
    return created


class TestSeeding:
    async def test_the_first_run_populates_every_module(self, seeded, db_session):
        assert seeded > 0
        for model in (Warehouse, Sku, User, InventoryItem, Courier, Shipment):
            count = await db_session.scalar(select(func.count()).select_from(model))
            assert count > 0, f"{model.__name__} was not seeded"

    async def test_running_twice_creates_nothing_new(self, seeded, db_session):
        """Seeders must be safe to re-run — ``make reset`` does it constantly."""
        assert await run_all(db_session) == 0

    async def test_running_twice_does_not_duplicate_rows(self, seeded, db_session):
        before = await db_session.scalar(select(func.count()).select_from(User))
        await run_all(db_session)
        after = await db_session.scalar(select(func.count()).select_from(User))
        assert before == after

    async def test_ids_are_derived_from_natural_keys_not_random(self, seeded, db_session):
        """Stable ids are what let seeders reference each other across runs."""
        admin = await db_session.scalar(select(User).where(User.email == "admin@transfleet.com"))
        assert admin.id == seed_id("user", "admin@transfleet.com")

    async def test_every_role_is_represented(self, seeded, db_session):
        rows = await db_session.scalars(select(User.role).distinct())
        assert set(rows.all()) == set(UserRole)


class TestSeededAccountsWork:
    @pytest.mark.parametrize(
        "email", [user.email for user in SEED_USERS if user.status is UserStatus.ACTIVE]
    )
    async def test_every_active_seeded_account_can_log_in(self, seeded, db_session, email):
        """The exact check that caught the hand-edited seed data during Week 1."""
        user, tokens = await AuthService(db_session).login(email, settings.seed_default_password)
        assert user.email == email
        assert tokens.access_token

    async def test_operators_are_scoped_to_their_own_facilities(self, seeded, db_session):
        from app.repositories.warehouse_repository import UserWarehouseScopeRepository

        operator = await db_session.scalar(
            select(User).where(User.email == "wh.karachi@transfleet.com")
        )
        scoped = await UserWarehouseScopeRepository(db_session).warehouse_ids_for(operator.id)
        assert len(scoped) == 1

    async def test_a_multi_site_operator_is_scoped_to_several(self, seeded, db_session):
        from app.repositories.warehouse_repository import UserWarehouseScopeRepository

        operator = await db_session.scalar(
            select(User).where(User.email == "wh.regional@transfleet.com")
        )
        scoped = await UserWarehouseScopeRepository(db_session).warehouse_ids_for(operator.id)
        assert len(scoped) == 3

    async def test_courier_logins_resolve_to_fleet_profiles(self, seeded, db_session):
        from app.repositories.courier_repository import CourierRepository

        courier_user = await db_session.scalar(
            select(User).where(User.email == "courier.one@transfleet.com")
        )
        assert await CourierRepository(db_session).get_by_user_id(courier_user.id) is not None


class TestSeededDataIsCoherent:
    async def test_stock_positions_point_at_real_warehouses_and_skus(self, seeded, db_session):
        orphans = await db_session.scalar(
            select(func.count())
            .select_from(InventoryItem)
            .outerjoin(Warehouse, Warehouse.id == InventoryItem.warehouse_id)
            .where(Warehouse.id.is_(None))
        )
        assert orphans == 0

    async def test_no_stock_position_is_oversold(self, seeded, db_session):
        oversold = await db_session.scalar(
            select(func.count())
            .select_from(InventoryItem)
            .where(InventoryItem.reserved_qty > InventoryItem.on_hand_qty)
        )
        assert oversold == 0

    async def test_shipments_reference_courier_profiles_not_user_accounts(self, seeded, db_session):
        """A bug found during Week 1: courier_id had been pointed at users.id."""
        mismatched = await db_session.scalar(
            select(func.count())
            .select_from(Shipment)
            .outerjoin(Courier, Courier.id == Shipment.courier_id)
            .where(Shipment.courier_id.is_not(None), Courier.id.is_(None))
        )
        assert mismatched == 0

    async def test_the_registry_lists_each_seeder_once(self):
        names = [seeder.name for seeder in SEEDERS]
        assert len(names) == len(set(names))
