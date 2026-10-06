from alembic import context

from inventory.config import settings
from inventory.models import Base
from sl_platform.migrations import run_migrations

run_migrations(
    Base.metadata, context.config.attributes.get("database_url") or settings.database_url
)
