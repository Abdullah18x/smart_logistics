"""Password reset tokens."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import IDENTITY_SCHEMA, Base, UUIDPrimaryKeyMixin


class PasswordResetToken(UUIDPrimaryKeyMixin, Base):
    """Single-use, hashed, short-lived password reset token."""

    __tablename__ = "password_reset_tokens"
    __table_args__ = ({"schema": IDENTITY_SCHEMA},)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{IDENTITY_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    @property
    def is_used(self) -> bool:
        return self.used_at is not None
