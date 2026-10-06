"""Identity's tables. Nothing outside this service may read them."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sl_platform.db import (
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    new_base,
    pg_enum,
    utcnow,
)
from sl_platform.roles import UserRole

Base = new_base()


class UserStatus(StrEnum):
    PENDING_ACTIVATION = "pending_activation"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_role_status", "role", "status"),
        Index(
            "ix_users_active_role", "role", "status", postgresql_where=text("deleted_at IS NULL")
        ),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )

    #: The courier fleet profile linked to this login, if any. Owned by the
    #: Courier service (Phase 2); copied here so it can ride in the token.
    courier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, unique=True)

    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    warehouse_assignments: Mapped[list[UserWarehouseAssignment]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="raise"
    )

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE and self.deleted_at is None


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """A refresh-token session. Only the SHA-256 hash is stored."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_id_expires_at", "user_id", "expires_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()"), nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(45))

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class UserWarehouseAssignment(UUIDPrimaryKeyMixin, Base):
    """Which warehouses an operator may act on.

    ``warehouse_id`` is a plain id: warehouses live in another service's
    database. It is checked against the Warehouse service when assigned, and
    the assignment is removed when a ``warehouse.deleted`` event arrives.
    """

    __tablename__ = "user_warehouse_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "warehouse_id", name="uq_user_warehouse_assignment"),
        Index("ix_user_warehouse_assignments_warehouse_id", "warehouse_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()"), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="warehouse_assignments", lazy="raise")


CONSTRAINT_MESSAGES = {
    "uq_users_email": "A user with this email already exists.",
    "ix_users_email": "A user with this email already exists.",
    "uq_user_warehouse_assignment": "The user is already assigned to this warehouse.",
    "uq_users_courier_id": "This courier profile is already linked to another user.",
}
