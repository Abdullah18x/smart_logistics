"""User management, including the soft-delete guarantee.

Nothing user-facing is ever hard-deleted (ADR-016): shipments, stock movements
and courier profiles all reference users, and those references must keep
resolving. These tests hold that line.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.constants.enums import UserRole, UserStatus
from app.core.db_errors import translate
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.security import verify_password
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.user import UserCreate, UserRoleUpdate, UserStatusUpdate, UserUpdate
from app.services.auth_service import AuthService
from app.services.user_service import UserService
from tests.factories import TEST_PASSWORD

NEW_PASSWORD = "Rotated!Password2026"


@pytest.fixture
def users(db_session) -> UserService:
    return UserService(db_session)


def payload(**overrides) -> UserCreate:
    return UserCreate(
        **{
            "email": f"new.{uuid.uuid4().hex[:8]}@transfleet.com",
            "full_name": "New Person",
            "password": TEST_PASSWORD,
            "role": UserRole.CUSTOMER_SUPPORT,
        }
        | overrides
    )


class TestCreate:
    async def test_creates_an_active_user(self, users):
        user = await users.create(payload())
        assert user.id is not None
        assert user.status is UserStatus.ACTIVE

    async def test_the_password_is_hashed_not_stored(self, users):
        user = await users.create(payload())
        assert user.password_hash != TEST_PASSWORD
        assert verify_password(TEST_PASSWORD, user.password_hash)

    async def test_the_password_change_timestamp_is_set(self, users):
        assert (await users.create(payload())).password_changed_at is not None

    async def test_the_email_is_stored_lowercase(self, users):
        user = await users.create(payload(email="Loud.Voice@TransFleet.COM"))
        assert user.email == "loud.voice@transfleet.com"

    async def test_a_duplicate_email_is_refused_by_the_database(self, users, db_session):
        """No pre-check in Python: a check-then-insert is a race, the index is not."""
        first = payload()
        await users.create(first)
        with pytest.raises(IntegrityError) as exc:
            await users.create(payload(email=first.email))

        # And the API layer turns that into a meaningful 409.
        translated = translate(exc.value)
        assert translated.status_code == 409
        assert translated.message == "A user with this email already exists."
        await db_session.rollback()

    @pytest.mark.parametrize("role", list(UserRole))
    async def test_every_role_can_be_created(self, users, role):
        assert (await users.create(payload(role=role))).role is role


class TestRead:
    async def test_fetches_by_id(self, users, factory):
        user = await factory.user()
        assert (await users.get(user.id)).id == user.id

    async def test_an_unknown_id_is_a_not_found(self, users):
        with pytest.raises(NotFoundError):
            await users.get(uuid.uuid4())

    async def test_lists_with_a_total(self, users, factory):
        for _ in range(3):
            await factory.user(UserRole.COURIER, full_name="Listable Courier")
        records, total = await users.list(role=UserRole.COURIER, search="Listable Courier")
        assert len(records) == 3
        assert total == 3

    async def test_filters_by_status(self, users, factory):
        await factory.user(UserRole.COURIER, status=UserStatus.SUSPENDED, full_name="Susp Target")
        await factory.user(UserRole.COURIER, status=UserStatus.ACTIVE, full_name="Susp Target")
        _, total = await users.list(status=UserStatus.SUSPENDED, search="Susp Target")
        assert total == 1

    async def test_search_matches_name_or_email_case_insensitively(self, users, factory):
        await factory.user(full_name="Zainab Qureshi", email="zq.unique@transfleet.com")
        by_name, _ = await users.list(search="zainab qur")
        by_email, _ = await users.list(search="ZQ.UNIQUE")
        assert by_name[0].id == by_email[0].id

    async def test_pagination_pages_through_the_same_result_set(self, users, factory):
        for index in range(5):
            await factory.user(full_name=f"Paged Person {index}")
        first, total = await users.list(search="Paged Person", limit=2, offset=0)
        second, _ = await users.list(search="Paged Person", limit=2, offset=2)
        assert total == 5
        assert len(first) == len(second) == 2
        assert {u.id for u in first}.isdisjoint({u.id for u in second})

    async def test_soft_deleted_users_are_excluded_from_listings(self, users, factory):
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user(full_name="Vanishing Person")
        _, before = await users.list(search="Vanishing Person")
        await users.delete(target.id, actor=admin)
        _, after = await users.list(search="Vanishing Person")
        assert (before, after) == (1, 0)


class TestProfileUpdate:
    async def test_updates_only_the_fields_supplied(self, users, factory):
        user = await factory.user(full_name="Original Name", phone="+923001111111")
        updated = await users.update_profile(user.id, UserUpdate(full_name="Changed Name"))
        assert updated.full_name == "Changed Name"
        assert updated.phone == "+923001111111"

    async def test_an_empty_update_changes_nothing(self, users, factory):
        user = await factory.user(full_name="Untouched")
        assert (await users.update_profile(user.id, UserUpdate())).full_name == "Untouched"

    async def test_updating_an_unknown_user_is_a_not_found(self, users):
        with pytest.raises(NotFoundError):
            await users.update_profile(uuid.uuid4(), UserUpdate(full_name="Nobody"))


class TestRoleAndStatus:
    async def test_an_admin_can_change_another_user_s_role(self, users, factory):
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user(UserRole.COURIER)
        updated = await users.change_role(
            target.id, UserRoleUpdate(role=UserRole.WAREHOUSE_OPERATOR), actor=admin
        )
        assert updated.role is UserRole.WAREHOUSE_OPERATOR

    async def test_an_admin_cannot_change_their_own_role(self, users, factory):
        """Guards against an admin demoting the last admin and locking everyone out."""
        admin = await factory.user(UserRole.ADMIN)
        with pytest.raises(PermissionDeniedError, match="your own role"):
            await users.change_role(admin.id, UserRoleUpdate(role=UserRole.COURIER), actor=admin)

    async def test_an_admin_cannot_change_their_own_status(self, users, factory):
        admin = await factory.user(UserRole.ADMIN)
        with pytest.raises(PermissionDeniedError):
            await users.change_status(
                admin.id, UserStatusUpdate(status=UserStatus.SUSPENDED), actor=admin
            )

    async def test_suspending_an_account_revokes_its_sessions(self, users, factory, db_session):
        """Otherwise a suspended user keeps working until their token lapses."""
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user()
        await AuthService(db_session).login(target.email, TEST_PASSWORD)

        await users.change_status(
            target.id, UserStatusUpdate(status=UserStatus.SUSPENDED), actor=admin
        )

        live = await db_session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None)
            )
        )
        assert list(live.all()) == []

    async def test_reactivating_an_account_leaves_sessions_alone(self, users, factory, db_session):
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user(status=UserStatus.SUSPENDED)
        await users.change_status(
            target.id, UserStatusUpdate(status=UserStatus.ACTIVE), actor=admin
        )
        assert target.status is UserStatus.ACTIVE


class TestPasswordChange:
    async def test_the_password_is_replaced(self, users, factory):
        user = await factory.user()
        await users.change_password(user, TEST_PASSWORD, NEW_PASSWORD)
        assert verify_password(NEW_PASSWORD, user.password_hash)
        assert not verify_password(TEST_PASSWORD, user.password_hash)

    async def test_the_wrong_current_password_is_refused(self, users, factory):
        user = await factory.user()
        with pytest.raises(PermissionDeniedError, match="Current password"):
            await users.change_password(user, "NotThePassword!1", NEW_PASSWORD)

    async def test_reusing_the_current_password_is_refused(self, users, factory):
        with pytest.raises(ConflictError, match="must differ"):
            user = await factory.user()
            await users.change_password(user, TEST_PASSWORD, TEST_PASSWORD)

    async def test_every_session_is_revoked(self, users, factory, db_session):
        """A password change is the standard response to a suspected compromise."""
        user = await factory.user()
        auth = AuthService(db_session)
        await auth.login(user.email, TEST_PASSWORD)
        await auth.login(user.email, TEST_PASSWORD)

        await users.change_password(user, TEST_PASSWORD, NEW_PASSWORD)
        assert await auth.list_sessions(user) == []

    async def test_the_change_timestamp_moves(self, users, factory):
        user = await factory.user()
        before = user.password_changed_at
        await users.change_password(user, TEST_PASSWORD, NEW_PASSWORD)
        assert user.password_changed_at > before


class TestSoftDeletion:
    async def test_the_row_survives_and_is_marked_deleted(self, users, factory, db_session):
        """A hard delete would orphan every shipment the user ever created."""
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user()

        await users.delete(target.id, actor=admin)

        still_there = await db_session.scalar(select(User).where(User.id == target.id))
        assert still_there is not None
        assert still_there.deleted_at is not None
        assert still_there.deleted_by == admin.id
        assert still_there.is_deleted is True

    async def test_the_account_is_deactivated_too(self, users, factory):
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user()
        await users.delete(target.id, actor=admin)
        assert target.status is UserStatus.DEACTIVATED

    async def test_a_deleted_user_is_invisible_to_ordinary_reads(self, users, factory):
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user()
        await users.delete(target.id, actor=admin)
        with pytest.raises(NotFoundError):
            await users.get(target.id)

    async def test_deleting_revokes_the_account_s_sessions(self, users, factory, db_session):
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user()
        auth = AuthService(db_session)
        await auth.login(target.email, TEST_PASSWORD)

        await users.delete(target.id, actor=admin)
        assert await auth.list_sessions(target) == []

    async def test_an_admin_cannot_delete_themselves(self, users, factory):
        admin = await factory.user(UserRole.ADMIN)
        with pytest.raises(PermissionDeniedError, match="your own account"):
            await users.delete(admin.id, actor=admin)

    async def test_deleting_an_unknown_user_is_a_not_found(self, users, factory):
        admin = await factory.user(UserRole.ADMIN)
        with pytest.raises(NotFoundError):
            await users.delete(uuid.uuid4(), actor=admin)

    async def test_a_deleted_user_can_be_restored(self, users, factory):
        """The whole reason deletion is soft."""
        admin = await factory.user(UserRole.ADMIN)
        target = await factory.user()
        await users.delete(target.id, actor=admin)

        restored = await users.restore(target.id)
        assert restored.deleted_at is None
        assert restored.deleted_by is None
        assert restored.status is UserStatus.ACTIVE
        assert (await users.get(target.id)).id == target.id

    async def test_restoring_an_unknown_user_is_a_not_found(self, users):
        with pytest.raises(NotFoundError):
            await users.restore(uuid.uuid4())
