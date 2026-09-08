"""Shared repository behaviour."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base


class BaseRepository[ModelT: Base]:
    """Persistence for one model.

    Repositories never commit: the service owns the transaction boundary so a
    single request can span several repositories atomically.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: uuid.UUID, *, include_deleted: bool = False) -> ModelT | None:
        entity = await self.session.get(self.model, entity_id)
        if entity is None:
            return None
        if not include_deleted and getattr(entity, "deleted_at", None) is not None:
            # A soft-deleted row is invisible unless explicitly asked for.
            return None
        return entity

    def active_only(self, statement):
        """Add the soft-delete filter when the model supports it."""
        deleted_at = getattr(self.model, "deleted_at", None)
        if deleted_at is None:
            return statement
        return statement.where(deleted_at.is_(None))

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def soft_delete(self, entity: ModelT, *, actor_id: uuid.UUID | None = None) -> ModelT:
        """Mark a row deleted.

        Nothing user-facing is hard-deleted: foreign keys, audit trails and
        historical shipments all point at rows that must keep resolving.
        """
        if not hasattr(entity, "deleted_at"):
            raise TypeError(f"{self.model.__name__} does not support soft deletion.")
        entity.deleted_at = datetime.now(UTC)
        entity.deleted_by = actor_id
        await self.session.flush()
        return entity

    async def restore(self, entity: ModelT) -> ModelT:
        entity.deleted_at = None
        entity.deleted_by = None
        await self.session.flush()
        return entity

    async def count(self) -> int:
        return await self.session.scalar(select(func.count()).select_from(self.model)) or 0
