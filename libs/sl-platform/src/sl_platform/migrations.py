"""Shared Alembic ``env.py`` body. Each service's env.py is three lines.

Every service owns its database outright, so migrations run against the
default ``public`` schema with no cross-service objects to worry about.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import MetaData, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config


def run_migrations(service_metadata: MetaData, database_url: str) -> None:
    import sl_platform.models  # noqa: F401  (registers the platform tables)
    from sl_platform.db import PlatformBase

    # The service's own tables plus the platform tables (outbox, dedupe, idempotency).
    target_metadata = [service_metadata, PlatformBase.metadata]
    config = context.config
    # Escape % so ConfigParser does not treat URL-encoded passwords as interpolation.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

    def configure(connection: Connection | None = None, **kwargs) -> None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            **kwargs,
        )

    if context.is_offline_mode():
        configure(url=database_url, literal_binds=True, dialect_opts={"paramstyle": "named"})
        with context.begin_transaction():
            context.run_migrations()
        return

    def do_run(connection: Connection) -> None:
        configure(connection)
        with context.begin_transaction():
            context.run_migrations()

    async def run_async() -> None:
        engine = async_engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        async with engine.connect() as connection:
            await connection.run_sync(do_run)
        await engine.dispose()

    asyncio.run(run_async())
