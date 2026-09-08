"""The user account: the root entity of the identity module."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import UserRole, UserStatus
from app.models.base import (
    IDENTITY_SCHEMA,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.refresh_token import RefreshToken
    from app.models.user_warehouse_assignment import UserWarehouseAssignment


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A person who can authenticate against SmartLogistics.

    Domain profiles (courier, warehouse operator) reference this row by id;
    per ADR-008 those live in their own schema with no cross-schema FK.
    """

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_role_status", "role", "status"),
        # Live rows only: the index stays the size of the active data no matter
        # how much soft-deleted history accumulates (ADR-016).
        Index(
            "ix_users_active_role",
            "role",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"schema": IDENTITY_SCHEMA},
    )

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role", IDENTITY_SCHEMA),
        nullable=False,
    )
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status", IDENTITY_SCHEMA),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )

    # Brute-force protection state.
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="raise"
    )
    warehouse_assignments: Mapped[list["UserWarehouseAssignment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="raise"
    )

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.id} {self.role}>"
