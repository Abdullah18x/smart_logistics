from alembic import context

from identity.config import settings
from identity.models import Base
from sl_platform.migrations import run_migrations

run_migrations(
    Base.metadata, context.config.attributes.get("database_url") or settings.database_url
)
