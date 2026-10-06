"""Declarative base, mixins and the per-service database handle.

Every service has its own Postgres instance; this module is how each one
talks to *its own* database and nothing else. Transaction rule: services and
repositories only ``flush``; the endpoint (or worker step) commits exactly
once, so a request's writes, its outbox events and its idempotency record land
in one transaction or not at all.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_base() -> type[DeclarativeBase]:
    """A fresh declarative base with its own registry and metadata.

    Each service calls this once for its own models, so two services can both
    have a ``warehouse_refs`` table without colliding — even when the
    end-to-end tests load them into one process.
    """

    class Base(DeclarativeBase):
        metadata = MetaData(naming_convention=NAMING_CONVENTION)

    return Base


#: Base for the platform tables every service database carries.
PlatformBase = new_base()


class UUIDPrimaryKeyMixin:
    """Opaque, non-enumerable identifiers."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    # Set Python-side as well as server-side, so a freshly flushed row can be
    # serialised without a lazy re-read (which fails under asyncio).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Nothing user-facing is hard-deleted: other services hold its id."""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


def pg_enum(enum_class: type[Enum], name: str) -> SAEnum:
    """A Postgres enum whose labels are the member values, not their names."""
    return SAEnum(
        enum_class,
        name=name,
        native_enum=True,
        values_callable=lambda enum: [member.value for member in enum],
    )


class Database:
    """One service's connection to its own database."""

    def __init__(
        self, url: str, *, echo: bool = False, pool_size: int = 5, max_overflow: int = 10
    ) -> None:
        self.engine = create_async_engine(
            url, echo=echo, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True
        )
        self.sessionmaker = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )

    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """FastAPI dependency: one session per request, rolled back on error.

        Nothing is committed here. The endpoint commits explicitly, so a commit
        failure becomes an error response instead of happening after the
        client was already told it succeeded.
        """
        async with self.sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def scope(self) -> AsyncGenerator[AsyncSession, None]:
        """Session for scripts and workers. Commits on success."""
        async with self.sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()
