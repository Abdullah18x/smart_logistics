"""Database access for Identity. Repositories flush; endpoints commit."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from identity.models import RefreshToken, User, UserStatus, UserWarehouseAssignment
from identity.security import hash_token
from sl_platform.db import utcnow
from sl_platform.roles import UserRole


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID, *, include_deleted: bool = False) -> User | None:
        user = await self.session.get(User, user_id)
        if user is None or (user.deleted_at is not None and not include_deleted):
            return None
        return user

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(
            select(User).where(User.email == email.lower(), User.deleted_at.is_(None))
        )

    async def list_users(
        self,
        *,
        role: UserRole | None,
        status: UserStatus | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[User], int]:
        filters = [User.deleted_at.is_(None)]
        if role is not None:
            filters.append(User.role == role)
        if status is not None:
            filters.append(User.status == status)
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(or_(User.full_name.ilike(pattern), User.email.ilike(pattern)))
        total = await self.session.scalar(select(func.count()).select_from(User).where(*filters))
        rows = await self.session.scalars(
            select(User)
            .where(*filters)
            .order_by(User.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), total or 0


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_token(
        self, raw_token: str, *, for_update: bool = False
    ) -> RefreshToken | None:
        statement = select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
        if for_update:
            # Two concurrent refreshes with the same token serialise here; the
            # second sees it already rotated and is treated as a replay.
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_active(self, user_id: uuid.UUID) -> list[RefreshToken]:
        rows = await self.session.scalars(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > utcnow(),
            )
            .order_by(RefreshToken.created_at.desc())
        )
        return list(rows.all())

    async def revoke_all(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        return result.rowcount or 0


class AssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def warehouse_ids_for(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        rows = await self.session.scalars(
            select(UserWarehouseAssignment.warehouse_id)
            .where(UserWarehouseAssignment.user_id == user_id)
            .order_by(UserWarehouseAssignment.warehouse_id)
        )
        return list(rows.all())

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserWarehouseAssignment]:
        rows = await self.session.scalars(
            select(UserWarehouseAssignment)
            .where(UserWarehouseAssignment.user_id == user_id)
            .order_by(UserWarehouseAssignment.created_at)
        )
        return list(rows.all())

    async def get(
        self, user_id: uuid.UUID, warehouse_id: uuid.UUID
    ) -> UserWarehouseAssignment | None:
        return await self.session.scalar(
            select(UserWarehouseAssignment).where(
                UserWarehouseAssignment.user_id == user_id,
                UserWarehouseAssignment.warehouse_id == warehouse_id,
            )
        )

    async def delete_for_warehouse(self, warehouse_id: uuid.UUID) -> list[uuid.UUID]:
        """Remove every assignment to a warehouse; return the affected user ids."""
        result = await self.session.execute(
            delete(UserWarehouseAssignment)
            .where(UserWarehouseAssignment.warehouse_id == warehouse_id)
            .returning(UserWarehouseAssignment.user_id)
        )
        return list(result.scalars().all())
