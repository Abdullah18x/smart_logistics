"""Persistence for user accounts."""

import uuid

from sqlalchemy import func, or_, select

from app.constants.enums import UserRole, UserStatus
from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email.lower()))

    async def email_exists(self, email: str, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(func.count()).select_from(User).where(User.email == email.lower())
        if exclude_id:
            stmt = stmt.where(User.id != exclude_id)
        return bool(await self.session.scalar(stmt))

    async def list_users(
        self,
        *,
        role: UserRole | None = None,
        status: UserStatus | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[User], int]:
        """Return one page of users and the total number of matches."""
        filters = []
        if role is not None:
            filters.append(User.role == role)
        if status is not None:
            filters.append(User.status == status)
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(or_(User.full_name.ilike(pattern), User.email.ilike(pattern)))

        total = await self.session.scalar(
            self.active_only(select(func.count()).select_from(User).where(*filters))
        )
        rows = await self.session.scalars(
            self.active_only(select(User).where(*filters))
            .order_by(User.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), total or 0
