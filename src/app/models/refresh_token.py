"""Refresh-token sessions."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import IDENTITY_SCHEMA, Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """A refresh-token session.

    Only the hash is stored, so a database leak cannot be replayed as a login.
    Rotation is tracked via ``replaced_by_id``: reuse of a rotated token is a
    theft signal and revokes the whole chain.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id_expires_at", "user_id", "expires_at"),
        {"schema": IDENTITY_SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{IDENTITY_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(45))

    user: Mapped["User"] = relationship(back_populates="refresh_tokens", lazy="raise")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
