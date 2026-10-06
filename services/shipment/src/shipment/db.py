from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from shipment.config import settings
from sl_platform.db import Database
from sl_platform.idempotency import IdempotencyStore

database = Database(
    settings.database_url,
    echo=settings.database_echo,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
)

idempotency = IdempotencyStore(
    database.sessionmaker,
    ttl_hours=settings.idempotency_ttl_hours,
    lease_seconds=settings.idempotency_lease_seconds,
)

SessionDep = Annotated[AsyncSession, Depends(database.session)]
