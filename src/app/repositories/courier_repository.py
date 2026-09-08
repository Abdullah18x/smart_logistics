"""Read access to courier profiles.

Courier management lands in Phase 2. This exists now only so a logged-in
courier can be resolved to their fleet profile for row-level scoping.
"""

import uuid

from sqlalchemy import select

from app.models.courier import Courier
from app.repositories.base import BaseRepository


class CourierRepository(BaseRepository[Courier]):
    model = Courier

    async def get_by_user_id(self, user_id: uuid.UUID) -> Courier | None:
        return await self.session.scalar(select(Courier).where(Courier.user_id == user_id))
