"""Repository behaviour that the services depend on but do not test directly."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import UserRole
from app.core.security import hash_token
from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.shipment_repository import ShipmentRepository
from app.repositories.user_repository import UserRepository
from app.repositories.warehouse_repository import (
    UserWarehouseScopeRepository,
    WarehouseRepository,
)


class TestSoftDeleteAwareness:
    async def test_a_soft_deleted_row_is_invisible_by_default(self, db_session, factory):
        users = UserRepository(db_session)
        user = await factory.user()
        await users.soft_delete(user)
        assert await users.get(user.id) is None

    async def test_it_can_still_be_fetched_deliberately(self, db_session, factory):
        """Restore needs to find the row it is restoring."""
        users = UserRepository(db_session)
        user = await factory.user()
        await users.soft_delete(user)
        assert (await users.get(user.id, include_deleted=True)).id == user.id

    async def test_the_actor_is_recorded(self, db_session, factory):
        users = UserRepository(db_session)
        user = await factory.user()
        actor = await factory.user(UserRole.ADMIN)
        await users.soft_delete(user, actor_id=actor.id)
        assert user.deleted_by == actor.id

    async def test_restoring_clears_the_deletion_marks(self, db_session, factory):
        users = UserRepository(db_session)
        user = await factory.user()
        await users.soft_delete(user, actor_id=user.id)
        await users.restore(user)
        assert user.deleted_at is None and user.deleted_by is None

    async def test_a_model_that_cannot_be_soft_deleted_says_so(self, db_session, factory):
        """Rather than silently doing nothing."""
        tokens = RefreshTokenRepository(db_session)
        user = await factory.user()
        token = RefreshToken(
            user_id=user.id,
            token_hash=hash_token("raw"),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        await tokens.add(token)
        with pytest.raises(TypeError, match="does not support soft deletion"):
            await tokens.soft_delete(token)

    async def test_the_active_filter_is_a_no_op_for_models_without_the_column(self, db_session):
        """So one query helper works across both kinds of table."""
        from sqlalchemy import select

        tokens = RefreshTokenRepository(db_session)
        statement = select(RefreshToken)
        assert str(tokens.active_only(statement)) == str(statement)

    async def test_an_unknown_id_returns_nothing(self, db_session):
        assert await UserRepository(db_session).get(uuid.uuid4()) is None

    async def test_the_base_repository_is_generic_over_its_model(self):
        assert issubclass(UserRepository, BaseRepository)
        assert UserRepository.model.__name__ == "User"


class TestUserRepository:
    async def test_lookup_by_email_lowercases_the_query(self, db_session, factory):
        users = UserRepository(db_session)
        user = await factory.user(email="finder@transfleet.com")
        assert (await users.get_by_email("FINDER@TRANSFLEET.COM")).id == user.id

    async def test_email_existence_can_exclude_one_row(self, db_session, factory):
        """So a user editing their own profile does not collide with themselves."""
        users = UserRepository(db_session)
        user = await factory.user(email="self@transfleet.com")
        assert await users.email_exists("self@transfleet.com") is True
        assert await users.email_exists("self@transfleet.com", exclude_id=user.id) is False


class TestRefreshTokenRepository:
    async def test_lookup_is_by_hash_not_by_the_raw_token(self, db_session, factory):
        tokens = RefreshTokenRepository(db_session)
        user = await factory.user()
        await tokens.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_token("the-raw-token"),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        assert await tokens.get_by_token("the-raw-token") is not None
        assert await tokens.get_by_token("a-different-token") is None

    async def test_revoking_all_reports_how_many_were_live(self, db_session, factory):
        tokens = RefreshTokenRepository(db_session)
        user = await factory.user()
        for index in range(3):
            await tokens.add(
                RefreshToken(
                    user_id=user.id,
                    token_hash=hash_token(f"token-{index}"),
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
        assert await tokens.revoke_all_for_user(user.id) == 3
        # Already revoked: nothing left to do.
        assert await tokens.revoke_all_for_user(user.id) == 0


class TestWarehouseRepository:
    async def test_lookup_by_code_uppercases_the_query(self, db_session, factory):
        warehouses = WarehouseRepository(db_session)
        warehouse = await factory.warehouse(code="KHI-77")
        assert (await warehouses.get_by_code("khi-77")).id == warehouse.id

    async def test_a_soft_deleted_facility_is_not_found_by_code(self, db_session, factory):
        warehouses = WarehouseRepository(db_session)
        warehouse = await factory.warehouse(code="KHI-78")
        await warehouses.soft_delete(warehouse)
        assert await warehouses.get_by_code("KHI-78") is None

    async def test_a_soft_deleted_zone_is_not_returned(self, db_session, factory):
        warehouses = WarehouseRepository(db_session)
        warehouse = await factory.warehouse()
        zone = await factory.zone(warehouse)
        assert await warehouses.get_zone(zone.id) is not None

        await warehouses.soft_delete(zone)
        assert await warehouses.get_zone(zone.id) is None

    async def test_zone_code_existence_ignores_deleted_zones(self, db_session, factory):
        """So a code freed by deletion can be reused."""
        warehouses = WarehouseRepository(db_session)
        warehouse = await factory.warehouse()
        zone = await factory.zone(warehouse, code="R1")
        assert await warehouses.zone_code_exists(warehouse.id, "r1") is True

        await warehouses.soft_delete(zone)
        assert await warehouses.zone_code_exists(warehouse.id, "R1") is False

    async def test_clearing_hours_removes_the_whole_week(self, db_session, factory):
        warehouses = WarehouseRepository(db_session)
        warehouse = await factory.warehouse()
        for day in range(1, 4):
            await factory.operating_hours(warehouse, day=day)

        await warehouses.clear_hours(warehouse.id)
        assert await warehouses.list_hours(warehouse.id) == []


class TestUserWarehouseScope:
    async def test_resolves_the_facilities_a_user_may_act_on(self, db_session, factory):
        scope = UserWarehouseScopeRepository(db_session)
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        first = await factory.warehouse()
        second = await factory.warehouse()
        await factory.assign(operator, first)
        await factory.assign(operator, second)

        assert set(await scope.warehouse_ids_for(operator.id)) == {first.id, second.id}

    async def test_an_unassigned_operator_resolves_to_an_empty_list(self, db_session, factory):
        """Empty, not None — restricted to nothing rather than unrestricted."""
        scope = UserWarehouseScopeRepository(db_session)
        operator = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        assert await scope.warehouse_ids_for(operator.id) == []


class TestCourierRepository:
    async def test_resolves_a_login_to_its_fleet_profile(self, db_session, factory):
        couriers = CourierRepository(db_session)
        user = await factory.user(UserRole.COURIER)
        courier = await factory.courier(user=user)
        assert (await couriers.get_by_user_id(user.id)).id == courier.id

    async def test_a_login_with_no_fleet_profile_resolves_to_nothing(self, db_session, factory):
        couriers = CourierRepository(db_session)
        user = await factory.user(UserRole.COURIER)
        assert await couriers.get_by_user_id(user.id) is None


class TestShipmentRepository:
    async def test_reference_numbers_come_from_a_sequence(self, db_session):
        shipments = ShipmentRepository(db_session)
        first = await shipments.next_reference_no()
        second = await shipments.next_reference_no()
        assert first != second
        assert first.startswith(f"SL-{datetime.now(UTC).year}-")

    async def test_lookup_by_reference_is_case_insensitive(self, db_session, factory):
        shipments = ShipmentRepository(db_session)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse, reference_no="SL-2026-999999")
        assert (await shipments.get_by_reference("sl-2026-999999")).id == shipment.id

    async def test_parcel_numbering_starts_at_one(self, db_session, factory):
        shipments = ShipmentRepository(db_session)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        assert await shipments.next_package_sequence(shipment.id) == 1

    async def test_parcel_numbering_continues_after_existing_parcels(self, db_session, factory):
        shipments = ShipmentRepository(db_session)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        await factory.package(shipment, sequence_no=1)
        await factory.package(shipment, sequence_no=7)
        assert await shipments.next_package_sequence(shipment.id) == 8

    async def test_detail_loads_the_collections_a_response_needs(self, db_session, factory):
        """The relationships are lazy='raise', so this must eager-load or blow up."""
        shipments = ShipmentRepository(db_session)
        warehouse = await factory.warehouse()
        sku = await factory.sku()
        shipment = await factory.shipment(warehouse)
        await factory.shipment_item(shipment, sku)
        await factory.package(shipment)

        # Detach everything so the query really has to load the collections,
        # rather than finding them already in the identity map.
        shipment_id = shipment.id
        await db_session.commit()
        db_session.expunge_all()

        loaded = await shipments.get_with_detail(shipment_id)
        assert len(loaded.items) == 1
        assert len(loaded.packages) == 1
        assert loaded.destination_address is not None

    async def test_soft_deleted_shipments_are_excluded_from_listings(self, db_session, factory):
        shipments = ShipmentRepository(db_session)
        warehouse = await factory.warehouse()
        shipment = await factory.shipment(warehouse)
        _, before = await shipments.list_shipments(origin_warehouse_id=warehouse.id)

        await shipments.soft_delete(shipment)
        _, after = await shipments.list_shipments(origin_warehouse_id=warehouse.id)
        assert (before, after) == (1, 0)
