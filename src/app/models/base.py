"""Declarative base and mixins shared by every persistence model."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Re-exported so every existing `from app.models.base import IDENTITY_SCHEMA`
# (and friends) keeps working — the constants themselves live in
# app.constants.database, which has no dependents of its own, so models can
# depend on it without models and constants ever needing each other.
from app.constants.database import (
    CATALOG_SCHEMA,
    COURIER_SCHEMA,
    IDENTITY_SCHEMA,
    INVENTORY_SCHEMA,
    PLATFORM_SCHEMA,
    SHIPMENT_SCHEMA,
    WAREHOUSE_SCHEMA,
)

__all__ = [
    "CATALOG_SCHEMA",
    "COURIER_SCHEMA",
    "IDENTITY_SCHEMA",
    "INVENTORY_SCHEMA",
    "NAMING_CONVENTION",
    "PLATFORM_SCHEMA",
    "SHIPMENT_SCHEMA",
    "WAREHOUSE_SCHEMA",
    "Base",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "pg_enum",
]

# Deterministic constraint names keep Alembic autogenerate diffs stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class SoftDeleteMixin:
    """Marks a row deleted without removing it.

    Nothing user-facing is ever hard-deleted: cross-schema references, audit
    trails and historical shipments all point at rows that must continue to
    resolve. Repositories exclude soft-deleted rows by default.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class UUIDPrimaryKeyMixin:
    """Opaque, non-enumerable identifiers."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set Python-side: a database-side onupdate would force a row re-read
    # after every UPDATE, which is lazy IO and fails under asyncio.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


def pg_enum(enum_class: type, name: str, schema: str) -> SAEnum:
    """A Postgres enum whose labels are the member values, not their names.

    Without ``values_callable`` SQLAlchemy stores member names ("ADMIN"), which
    then disagree with any ``server_default`` written as a value ("admin").
    """
    return SAEnum(
        enum_class,
        name=name,
        schema=schema,
        native_enum=True,
        values_callable=lambda enum: [member.value for member in enum],
    )
