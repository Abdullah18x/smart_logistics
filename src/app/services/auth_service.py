"""Authentication: login, refresh rotation and logout."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    AuthenticationError,
)
from app.core.security import generate_token, hash_token, verify_password
from app.core.tokens import create_access_token, refresh_token_expiry
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository


class IssuedTokens:
    """Tokens returned to the client. The raw refresh token exists only here."""

    def __init__(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def login(
        self,
        email: str,
        password: str,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[User, IssuedTokens]:
        user = await self.users.get_by_email(email)

        # Verify against a dummy hash when the user is missing so that response
        # time does not reveal whether an account exists.
        if user is None:
            verify_password(password, _DUMMY_HASH)
            raise AuthenticationError("Incorrect email or password.")

        if user.locked_until and user.locked_until > datetime.now(UTC):
            raise AccountLockedError("Account is temporarily locked. Try again later.")

        if not verify_password(password, user.password_hash):
            await self._register_failed_attempt(user)
            await self.session.commit()
            raise AuthenticationError("Incorrect email or password.")

        if not user.is_active:
            raise AccountInactiveError("Account is not active.")

        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = datetime.now(UTC)

        tokens, _ = await self._issue_tokens(user, user_agent=user_agent, ip_address=ip_address)
        await self.session.commit()
        return user, tokens

    async def refresh(
        self,
        raw_token: str,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[User, IssuedTokens]:
        stored = await self.refresh_tokens.get_by_token(raw_token)
        if stored is None:
            raise AuthenticationError("Refresh token is invalid.")

        if stored.is_revoked:
            # A revoked token being replayed means it leaked. Kill every session
            # for that user rather than just rejecting this request.
            await self.refresh_tokens.revoke_all_for_user(stored.user_id)
            await self.session.commit()
            raise AuthenticationError("Refresh token has been revoked.")

        if stored.expires_at <= datetime.now(UTC):
            raise AuthenticationError("Refresh token has expired.")

        user = await self.users.get(stored.user_id)
        if user is None or not user.is_active:
            raise AccountInactiveError("Account is not active.")

        tokens, replacement = await self._issue_tokens(
            user, user_agent=user_agent, ip_address=ip_address
        )
        # Rotation: the old token is retired and points at its replacement.
        await self.refresh_tokens.revoke(stored, replaced_by_id=replacement.id)
        await self.session.commit()
        return user, tokens

    async def logout(self, raw_token: str, *, user: User) -> None:
        """Revoke a single session."""
        stored = await self.refresh_tokens.get_by_token(raw_token)
        if stored is None or stored.user_id != user.id:
            # Do not disclose whether the token exists but belongs elsewhere.
            raise AuthenticationError("Refresh token is invalid.")
        if not stored.is_revoked:
            await self.refresh_tokens.revoke(stored)
        await self.session.commit()

    async def logout_all(self, user: User) -> int:
        count = await self.refresh_tokens.revoke_all_for_user(user.id)
        await self.session.commit()
        return count

    async def list_sessions(self, user: User) -> list[RefreshToken]:
        return await self.refresh_tokens.list_active_for_user(user.id)

    async def _issue_tokens(
        self, user: User, *, user_agent: str | None, ip_address: str | None
    ) -> tuple[IssuedTokens, RefreshToken]:
        access_token, expires_in = create_access_token(user.id, user.role)
        raw_refresh = generate_token()
        stored = await self.refresh_tokens.add(
            RefreshToken(
                id=uuid.uuid4(),
                user_id=user.id,
                token_hash=hash_token(raw_refresh),
                expires_at=refresh_token_expiry(),
                user_agent=user_agent,
                ip_address=ip_address,
            )
        )
        return IssuedTokens(access_token, raw_refresh, expires_in), stored

    async def _register_failed_attempt(self, user: User) -> None:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.max_failed_login_attempts:
            user.locked_until = datetime.now(UTC) + timedelta(
                minutes=settings.account_lockout_minutes
            )
            user.failed_login_attempts = 0


# Cost-equivalent hash used to keep timing constant for unknown emails.
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$c21hcnRsb2dpc3RpY3M$0000000000000000000000000000000000000000000"
)
