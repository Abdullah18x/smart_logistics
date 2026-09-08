"""User management business logic."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import UserRole, UserStatus
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate, UserRoleUpdate, UserStatusUpdate, UserUpdate


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def get(self, user_id: uuid.UUID) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def list(
        self,
        *,
        role: UserRole | None = None,
        status: UserStatus | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[User], int]:
        return await self.users.list_users(
            role=role, status=status, search=search, limit=limit, offset=offset
        )

    async def create(self, payload: UserCreate) -> User:
        # The unique index on email is authoritative; a pre-check would be a
        # race. ``core.db_errors`` turns the violation into the same 409.
        user = User(
            email=payload.email,
            full_name=payload.full_name,
            phone=payload.phone,
            password_hash=hash_password(payload.password),
            password_changed_at=datetime.now(UTC),
            role=payload.role,
            status=UserStatus.ACTIVE,
        )
        await self.users.add(user)
        await self.session.commit()
        return user

    async def update_profile(self, user_id: uuid.UUID, payload: UserUpdate) -> User:
        user = await self.get(user_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        await self.session.commit()
        return user

    async def change_role(
        self, user_id: uuid.UUID, payload: UserRoleUpdate, *, actor: User
    ) -> User:
        user = await self.get(user_id)
        if user.id == actor.id:
            # Prevents an admin locking the system out of its last admin.
            raise PermissionDeniedError("You cannot change your own role.")
        user.role = payload.role
        await self.session.commit()
        return user

    async def change_status(
        self, user_id: uuid.UUID, payload: UserStatusUpdate, *, actor: User
    ) -> User:
        user = await self.get(user_id)
        if user.id == actor.id:
            raise PermissionDeniedError("You cannot change your own status.")

        user.status = payload.status
        if payload.status is not UserStatus.ACTIVE:
            # A deactivated account must not keep working via an existing session.
            await self.refresh_tokens.revoke_all_for_user(user.id)
        await self.session.commit()
        return user

    async def change_password(self, user: User, current_password: str, new_password: str) -> None:
        if not verify_password(current_password, user.password_hash):
            raise PermissionDeniedError("Current password is incorrect.")
        if verify_password(new_password, user.password_hash):
            raise ConflictError("New password must differ from the current one.")

        user.password_hash = hash_password(new_password)
        user.password_changed_at = datetime.now(UTC)
        # Every other session is invalidated: a password change is the standard
        # response to a suspected compromise.
        await self.refresh_tokens.revoke_all_for_user(user.id)
        await self.session.commit()

    async def delete(self, user_id: uuid.UUID, *, actor: User) -> None:
        """Soft delete.

        Shipments, stock movements and courier profiles reference users by
        foreign key, so the row stays and is marked deleted. Sessions are
        revoked so the account stops working immediately.
        """
        user = await self.get(user_id)
        if user.id == actor.id:
            raise PermissionDeniedError("You cannot delete your own account.")
        user.status = UserStatus.DEACTIVATED
        await self.refresh_tokens.revoke_all_for_user(user.id)
        await self.users.soft_delete(user, actor_id=actor.id)
        await self.session.commit()

    async def restore(self, user_id: uuid.UUID) -> User:
        user = await self.users.get(user_id, include_deleted=True)
        if user is None:
            raise NotFoundError("User not found.")
        await self.users.restore(user)
        user.status = UserStatus.ACTIVE
        await self.session.commit()
        return user
