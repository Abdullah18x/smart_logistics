"""Login, refresh rotation and logout, against the real database.

Authentication is where the security-relevant decisions live, so the negative
paths — lockout, revocation, token theft — carry more weight here than the
happy path.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.constants.enums import UserRole, UserStatus
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    AuthenticationError,
)
from app.core.security import hash_token
from app.core.tokens import decode_access_token
from app.models.refresh_token import RefreshToken
from app.services.auth_service import AuthService
from tests.factories import TEST_PASSWORD


@pytest.fixture
def auth(db_session) -> AuthService:
    return AuthService(db_session)


class TestSuccessfulLogin:
    async def test_returns_the_user_and_a_token_pair(self, auth, factory):
        user = await factory.user(UserRole.ADMIN)
        logged_in, tokens = await auth.login(user.email, TEST_PASSWORD)
        assert logged_in.id == user.id
        assert tokens.access_token and tokens.refresh_token
        assert tokens.expires_in > 0

    async def test_the_access_token_identifies_the_user_and_their_role(self, auth, factory):
        """The role travels in the token so authorisation needs no extra read."""
        user = await factory.user(UserRole.WAREHOUSE_OPERATOR)
        _, tokens = await auth.login(user.email, TEST_PASSWORD)
        payload = decode_access_token(tokens.access_token)
        assert payload["sub"] == str(user.id)
        assert payload["role"] == UserRole.WAREHOUSE_OPERATOR.value

    async def test_a_mixed_case_email_can_still_log_in(self, auth, factory):
        """The lookup lowercases what the caller typed before matching."""
        user = await factory.user(email="ops.lead@transfleet.com")
        logged_in, _ = await auth.login("Ops.Lead@TransFleet.com", TEST_PASSWORD)
        assert logged_in.id == user.id

    async def test_lowercase_storage_is_guaranteed_by_the_schema_not_the_column(
        self, auth, factory
    ):
        """A documented gap, pinned here so it cannot regress silently.

        ``UserRepository.get_by_email`` lowercases the *query*, so it can only
        find rows that were stored lowercase. Nothing in the model or the
        database enforces that — only ``UserCreate.normalise_email`` does. Every
        write path goes through that schema today, so the invariant holds, but a
        future path that inserts a User directly would create a row nobody can
        log in as. A citext column or a case-insensitive unique index would move
        the guarantee down to where the unique constraint already lives.
        """
        stored_with_capitals = await factory.user(email="Capitals@transfleet.com")
        with pytest.raises(AuthenticationError):
            await auth.login(stored_with_capitals.email, TEST_PASSWORD)

    async def test_last_login_is_recorded(self, auth, factory):
        user = await factory.user()
        assert user.last_login_at is None
        await auth.login(user.email, TEST_PASSWORD)
        assert user.last_login_at is not None

    async def test_only_the_hash_of_the_refresh_token_is_stored(self, auth, factory, db_session):
        """A database leak must not hand an attacker a working session."""
        user = await factory.user()
        _, tokens = await auth.login(user.email, TEST_PASSWORD)

        stored = await db_session.scalar(
            select(RefreshToken).where(RefreshToken.user_id == user.id)
        )
        assert stored.token_hash == hash_token(tokens.refresh_token)
        assert tokens.refresh_token not in stored.token_hash

    async def test_the_session_records_where_it_came_from(self, auth, factory, db_session):
        user = await factory.user()
        await auth.login(user.email, TEST_PASSWORD, user_agent="curl/8.4", ip_address="10.0.0.9")
        stored = await db_session.scalar(
            select(RefreshToken).where(RefreshToken.user_id == user.id)
        )
        assert stored.user_agent == "curl/8.4"
        assert stored.ip_address == "10.0.0.9"

    async def test_a_successful_login_clears_earlier_failures(self, auth, factory):
        user = await factory.user()
        with pytest.raises(AuthenticationError):
            await auth.login(user.email, "WrongPassword!1")
        assert user.failed_login_attempts == 1

        await auth.login(user.email, TEST_PASSWORD)
        assert user.failed_login_attempts == 0
        assert user.locked_until is None

    async def test_each_login_opens_a_separate_session(self, auth, factory, db_session):
        user = await factory.user()
        await auth.login(user.email, TEST_PASSWORD)
        await auth.login(user.email, TEST_PASSWORD)
        count = await db_session.scalar(
            select(func.count()).select_from(RefreshToken).where(RefreshToken.user_id == user.id)
        )
        assert count == 2


class TestFailedLogin:
    async def test_a_wrong_password_is_rejected(self, auth, factory):
        user = await factory.user()
        with pytest.raises(AuthenticationError):
            await auth.login(user.email, "WrongPassword!1")

    async def test_an_unknown_email_gives_the_same_error_as_a_wrong_password(self, auth, factory):
        """Different messages would let an attacker enumerate accounts."""
        user = await factory.user()
        with pytest.raises(AuthenticationError) as unknown:
            await auth.login("nobody@transfleet.com", TEST_PASSWORD)
        with pytest.raises(AuthenticationError) as wrong:
            await auth.login(user.email, "WrongPassword!1")
        assert str(unknown.value) == str(wrong.value)

    async def test_failures_are_counted(self, auth, factory, monkeypatch, app_settings):
        monkeypatch.setattr(app_settings, "max_failed_login_attempts", 5)
        user = await factory.user()
        for expected in (1, 2, 3):
            with pytest.raises(AuthenticationError):
                await auth.login(user.email, "WrongPassword!1")
            assert user.failed_login_attempts == expected

    async def test_the_account_locks_after_the_configured_number_of_failures(
        self, auth, factory, monkeypatch, app_settings
    ):
        monkeypatch.setattr(app_settings, "max_failed_login_attempts", 3)
        user = await factory.user()

        for _ in range(2):
            with pytest.raises(AuthenticationError):
                await auth.login(user.email, "WrongPassword!1")
        assert user.locked_until is None

        with pytest.raises(AuthenticationError):
            await auth.login(user.email, "WrongPassword!1")
        assert user.locked_until is not None
        assert user.locked_until > datetime.now(UTC)
        # The counter resets, so the next lock takes a full run of failures.
        assert user.failed_login_attempts == 0

    async def test_a_locked_account_rejects_even_the_correct_password(
        self, auth, factory, monkeypatch, app_settings
    ):
        """This is the whole point of the lockout."""
        monkeypatch.setattr(app_settings, "max_failed_login_attempts", 1)
        user = await factory.user()
        with pytest.raises(AuthenticationError):
            await auth.login(user.email, "WrongPassword!1")

        with pytest.raises(AccountLockedError):
            await auth.login(user.email, TEST_PASSWORD)

    async def test_an_expired_lock_no_longer_blocks_login(self, auth, factory):
        user = await factory.user(locked_until=datetime.now(UTC) - timedelta(minutes=1))
        logged_in, _ = await auth.login(user.email, TEST_PASSWORD)
        assert logged_in.id == user.id

    @pytest.mark.parametrize(
        "status", [UserStatus.SUSPENDED, UserStatus.DEACTIVATED, UserStatus.PENDING_ACTIVATION]
    )
    async def test_an_inactive_account_cannot_log_in(self, auth, factory, status):
        user = await factory.user(status=status)
        with pytest.raises(AccountInactiveError):
            await auth.login(user.email, TEST_PASSWORD)

    async def test_the_password_is_checked_before_the_account_status(self, auth, factory):
        """A suspended account must not confirm its password to a guesser."""
        user = await factory.user(status=UserStatus.SUSPENDED)
        with pytest.raises(AuthenticationError) as exc:
            await auth.login(user.email, "WrongPassword!1")
        assert not isinstance(exc.value, AccountInactiveError)


class TestRefreshRotation:
    async def test_a_refresh_returns_a_new_pair(self, auth, factory):
        user = await factory.user()
        _, first = await auth.login(user.email, TEST_PASSWORD)
        _, second = await auth.refresh(first.refresh_token)
        assert second.refresh_token != first.refresh_token
        assert second.access_token != first.access_token

    async def test_the_presented_token_is_revoked_and_points_at_its_replacement(
        self, auth, factory, db_session
    ):
        user = await factory.user()
        _, first = await auth.login(user.email, TEST_PASSWORD)
        _, second = await auth.refresh(first.refresh_token)

        old = await db_session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(first.refresh_token))
        )
        new = await db_session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(second.refresh_token))
        )
        assert old.is_revoked
        assert old.replaced_by_id == new.id
        assert not new.is_revoked

    async def test_the_new_token_works(self, auth, factory):
        user = await factory.user()
        _, first = await auth.login(user.email, TEST_PASSWORD)
        _, second = await auth.refresh(first.refresh_token)
        _, third = await auth.refresh(second.refresh_token)
        assert third.refresh_token not in (first.refresh_token, second.refresh_token)

    async def test_replaying_a_rotated_token_kills_every_session(self, auth, factory, db_session):
        """A token used twice has leaked; the safe response is to end all sessions."""
        user = await factory.user()
        _, first = await auth.login(user.email, TEST_PASSWORD)
        await auth.login(user.email, TEST_PASSWORD)  # a second device
        _, rotated = await auth.refresh(first.refresh_token)

        with pytest.raises(AuthenticationError, match="revoked"):
            await auth.refresh(first.refresh_token)

        live = await db_session.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        )
        assert live == 0
        # Including the replacement that was legitimately issued moments earlier.
        with pytest.raises(AuthenticationError):
            await auth.refresh(rotated.refresh_token)

    async def test_an_unknown_token_is_rejected(self, auth):
        with pytest.raises(AuthenticationError, match="invalid"):
            await auth.refresh("a-token-that-was-never-issued")

    async def test_an_expired_token_is_rejected(self, auth, factory, db_session):
        user = await factory.user()
        _, tokens = await auth.login(user.email, TEST_PASSWORD)
        stored = await db_session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(tokens.refresh_token))
        )
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.commit()

        with pytest.raises(AuthenticationError, match="expired"):
            await auth.refresh(tokens.refresh_token)

    async def test_a_suspended_user_cannot_refresh(self, auth, factory):
        """Suspension must take effect without waiting for the access token to lapse."""
        user = await factory.user()
        _, tokens = await auth.login(user.email, TEST_PASSWORD)
        user.status = UserStatus.SUSPENDED
        await auth.session.commit()

        with pytest.raises(AccountInactiveError):
            await auth.refresh(tokens.refresh_token)


class TestLogout:
    async def test_logout_revokes_only_the_presented_session(self, auth, factory, db_session):
        user = await factory.user()
        _, first = await auth.login(user.email, TEST_PASSWORD)
        _, second = await auth.login(user.email, TEST_PASSWORD)

        await auth.logout(first.refresh_token, user=user)

        live = await db_session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
            )
        )
        remaining = list(live.all())
        assert len(remaining) == 1
        assert remaining[0].token_hash == hash_token(second.refresh_token)

    async def test_a_revoked_token_can_no_longer_refresh(self, auth, factory):
        user = await factory.user()
        _, tokens = await auth.login(user.email, TEST_PASSWORD)
        await auth.logout(tokens.refresh_token, user=user)
        with pytest.raises(AuthenticationError):
            await auth.refresh(tokens.refresh_token)

    async def test_logging_out_twice_is_not_an_error(self, auth, factory):
        user = await factory.user()
        _, tokens = await auth.login(user.email, TEST_PASSWORD)
        await auth.logout(tokens.refresh_token, user=user)
        await auth.logout(tokens.refresh_token, user=user)

    async def test_one_user_cannot_revoke_another_user_s_session(self, auth, factory):
        """And is not told whether the token exists."""
        victim = await factory.user()
        attacker = await factory.user()
        _, tokens = await auth.login(victim.email, TEST_PASSWORD)

        with pytest.raises(AuthenticationError, match="invalid"):
            await auth.logout(tokens.refresh_token, user=attacker)

    async def test_an_unknown_token_is_rejected(self, auth, factory):
        user = await factory.user()
        with pytest.raises(AuthenticationError):
            await auth.logout("never-issued", user=user)

    async def test_logout_all_revokes_every_session_and_reports_the_count(self, auth, factory):
        user = await factory.user()
        for _ in range(3):
            await auth.login(user.email, TEST_PASSWORD)

        assert await auth.logout_all(user) == 3
        assert await auth.list_sessions(user) == []

    async def test_logout_all_leaves_other_users_alone(self, auth, factory):
        user = await factory.user()
        bystander = await factory.user()
        await auth.login(user.email, TEST_PASSWORD)
        await auth.login(bystander.email, TEST_PASSWORD)

        await auth.logout_all(user)
        assert len(await auth.list_sessions(bystander)) == 1


class TestSessionListing:
    async def test_lists_the_caller_s_live_sessions(self, auth, factory):
        user = await factory.user()
        await auth.login(user.email, TEST_PASSWORD, user_agent="Chrome")
        await auth.login(user.email, TEST_PASSWORD, user_agent="iOS")
        agents = {s.user_agent for s in await auth.list_sessions(user)}
        assert agents == {"Chrome", "iOS"}

    async def test_revoked_sessions_are_hidden(self, auth, factory):
        user = await factory.user()
        _, tokens = await auth.login(user.email, TEST_PASSWORD)
        await auth.login(user.email, TEST_PASSWORD)
        await auth.logout(tokens.refresh_token, user=user)
        assert len(await auth.list_sessions(user)) == 1

    async def test_expired_sessions_are_hidden(self, auth, factory, db_session):
        user = await factory.user()
        _, tokens = await auth.login(user.email, TEST_PASSWORD)
        stored = await db_session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(tokens.refresh_token))
        )
        stored.expires_at = datetime.now(UTC) - timedelta(days=1)
        await db_session.commit()
        assert await auth.list_sessions(user) == []
