from alembic import context

from sl_platform.migrations import run_migrations
from warehouse.config import settings
from warehouse.models import Base

run_migrations(
    Base.metadata, context.config.attributes.get("database_url") or settings.database_url
)
