"""Login, refresh rotation and logout. Methods flush; the endpoint commits."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from identity.config import Settings
from identity.keys import SigningKey
from identity.models import RefreshToken, User
from identity.repository import AssignmentRepository, RefreshTokenRepository, UserRepository
from identity.security import DUMMY_HASH, generate_token, hash_token, verify_password
from sl_platform.db import utcnow
from sl_platform.errors import AuthenticationError
from sl_platform.roles import UserRole


class AccountLockedError(AuthenticationError):
    """The account is temporarily locked after too many failed attempts."""

    code = "account_locked"


class AccountInactiveError(AuthenticationError):
    """The account is not active."""

    code = "account_inactive"


class TokenReuseError(AuthenticationError):
    """A rotated refresh token was replayed; every session was revoked."""

    code = "refresh_token_reused"


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings, key: SigningKey) -> None:
        self.session = session
        self.settings = settings
        self.key = key
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)
        self.assignments = AssignmentRepository(session)

    async def login(
        self, email: str, password: str, *, user_agent: str | None, ip_address: str | None
    ) -> tuple[User, IssuedTokens]:
        user = await self.users.get_by_email(email)
        if user is None:
            verify_password(password, DUMMY_HASH)  # equalise timing
            raise AuthenticationError("Incorrect email or password.")
        if user.locked_until and user.locked_until > utcnow():
            raise AccountLockedError("Account is temporarily locked. Try again later.")
        if not verify_password(password, user.password_hash):
            self._register_failed_attempt(user)
            # The failed attempt must persist even though the request fails.
            await self.session.commit()
            raise AuthenticationError("Incorrect email or password.")
        if not user.is_active:
            raise AccountInactiveError("Account is not active.")

        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = utcnow()
        tokens, _ = await self._issue(user, user_agent=user_agent, ip_address=ip_address)
        return user, tokens

    async def refresh(
        self, raw_token: str, *, user_agent: str | None, ip_address: str | None
    ) -> IssuedTokens:
        stored = await self.tokens.get_by_token(raw_token, for_update=True)
        if stored is None:
            raise AuthenticationError("Refresh token is invalid.")
        if stored.is_revoked:
            # A rotated token coming back means it leaked. Kill every session.
            await self.tokens.revoke_all(stored.user_id)
            await self.session.commit()
            raise TokenReuseError()
        if stored.expires_at <= utcnow():
            raise AuthenticationError("Refresh token has expired.")
        user = await self.users.get(stored.user_id)
        if user is None or not user.is_active:
            raise AccountInactiveError("Account is not active.")

        tokens, replacement = await self._issue(user, user_agent=user_agent, ip_address=ip_address)
        stored.revoked_at = utcnow()
        stored.replaced_by_id = replacement.id
        await self.session.flush()
        return tokens

    async def logout(self, raw_token: str, *, user_id: uuid.UUID) -> None:
        stored = await self.tokens.get_by_token(raw_token)
        if stored is None or stored.user_id != user_id:
            raise AuthenticationError("Refresh token is invalid.")
        if not stored.is_revoked:
            stored.revoked_at = utcnow()
            await self.session.flush()

    async def logout_all(self, user_id: uuid.UUID) -> int:
        return await self.tokens.revoke_all(user_id)

    async def _issue(
        self, user: User, *, user_agent: str | None, ip_address: str | None
    ) -> tuple[IssuedTokens, RefreshToken]:
        warehouse_ids = (
            await self.assignments.warehouse_ids_for(user.id)
            if user.role is UserRole.WAREHOUSE_OPERATOR
            else []
        )
        ttl = timedelta(minutes=self.settings.access_token_ttl_minutes)
        access = self.key.sign_access_token(
            user_id=user.id,
            role=user.role.value,
            name=user.full_name,
            warehouse_ids=warehouse_ids,
            courier_id=user.courier_id,
            ttl=ttl,
            issuer=self.settings.jwt_issuer,
            audience=self.settings.jwt_audience,
        )
        raw_refresh = generate_token()
        stored = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=hash_token(raw_refresh),
            expires_at=utcnow() + timedelta(days=self.settings.refresh_token_ttl_days),
            user_agent=(user_agent or "")[:255] or None,
            ip_address=ip_address,
        )
        self.session.add(stored)
        await self.session.flush()
        return IssuedTokens(access, raw_refresh, int(ttl.total_seconds())), stored

    def _register_failed_attempt(self, user: User) -> None:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= self.settings.max_failed_login_attempts:
            user.locked_until = utcnow() + timedelta(minutes=self.settings.account_lockout_minutes)
            user.failed_login_attempts = 0
