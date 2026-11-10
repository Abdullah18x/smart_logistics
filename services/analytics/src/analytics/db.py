from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.config import settings
from sl_platform.db import Database

database = Database(
    settings.database_url,
    echo=settings.database_echo,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
)

SessionDep = Annotated[AsyncSession, Depends(database.session)]
