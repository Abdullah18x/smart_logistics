"""Persistence for refresh-token sessions."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.core.security import hash_token
from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_token(self, raw_token: str) -> RefreshToken | None:
        """Look up by hash: the raw token is never stored."""
        return await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
        )

    async def list_active_for_user(self, user_id: uuid.UUID) -> list[RefreshToken]:
        rows = await self.session.scalars(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.now(UTC),
            )
            .order_by(RefreshToken.created_at.desc())
        )
        return list(rows.all())

    async def revoke(self, token: RefreshToken, replaced_by_id: uuid.UUID | None = None) -> None:
        token.revoked_at = datetime.now(UTC)
        token.replaced_by_id = replaced_by_id
        await self.session.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Revoke every live session. Used on logout-all and password change."""
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()
        return result.rowcount or 0
