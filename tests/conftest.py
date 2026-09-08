"""Test configuration and shared fixtures.

Everything below runs against a real PostgreSQL instance, never SQLite. The
application leans on behaviour a substitute cannot reproduce — generated
columns, native enums, schema-qualified tables, sequences and
``SELECT ... FOR UPDATE`` — so a green run against SQLite would prove nothing
about the code that actually ships.

The test database is dropped and rebuilt **from the migrations** once per
session, which means every run also verifies that the migration chain applies
cleanly to an empty database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Environment. This must happen before anything from ``app`` is imported: the
# settings object and the engine are both built at import time, so the test
# database name has to be in the environment first.
# ---------------------------------------------------------------------------

_DEFAULT_URL = "postgresql+asyncpg://smartlogistics:smartlogistics@localhost:5432/smartlogistics"


def _dotenv_value(key: str) -> str | None:
    """Read one key from .env without importing the settings module."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            # Trim inline comments and surrounding quotes.
            return value.split("#")[0].strip().strip("'\"")
    return None


def _test_database_url() -> str:
    base = os.environ.get("DATABASE_URL") or _dotenv_value("DATABASE_URL") or _DEFAULT_URL
    url = make_url(base)
    name = url.database or "smartlogistics"
    if not name.endswith("_test"):
        name = f"{name}_test"
    return url.set(database=name).render_as_string(hide_password=False)


TEST_DATABASE_URL = _test_database_url()

# Guard against a mistyped URL wiping the development database.
assert (make_url(TEST_DATABASE_URL).database or "").endswith("_test"), (
    "refusing to run tests against a database whose name does not end in _test"
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["DATABASE_ECHO"] = "false"
# A fixed key so token assertions are reproducible, and never the production one.
os.environ["JWT_SECRET_KEY"] = "test-only-signing-key-not-used-anywhere-else"
os.environ["SEED_DEFAULT_PASSWORD"] = "SmartLogistics!2026"

# Imports below deliberately follow the environment setup above.
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings  # noqa: E402

pytest_plugins = ["tests.factories"]


# ---------------------------------------------------------------------------
# Database lifecycle
# ---------------------------------------------------------------------------


async def _recreate_database() -> None:
    """Drop and recreate the test database from the maintenance connection."""
    import asyncpg

    url = make_url(TEST_DATABASE_URL)
    name = url.database
    connection = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host or "localhost",
        port=url.port or 5432,
        database="postgres",
    )
    try:
        # Terminate stragglers from an interrupted run, or DROP will block.
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def database() -> str:
    """A migrated, empty test database. Session-scoped: built once.

    Synchronous on purpose — Alembic's ``env.py`` calls ``asyncio.run`` itself,
    which cannot be nested inside a running event loop.
    """
    from alembic import command
    from alembic.config import Config

    try:
        asyncio.run(_recreate_database())
    except Exception as exc:  # pragma: no cover - environment problem, not a defect
        pytest.skip(f"PostgreSQL is not reachable for tests: {exc.__class__.__name__}: {exc}")

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def engine(database: str):
    """A fresh engine per test, so no connection outlives its event loop."""
    from sqlalchemy.pool import NullPool

    created = create_async_engine(database, poolclass=NullPool, future=True)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
async def connection(engine):
    """One connection inside an outer transaction that is always rolled back.

    This is what keeps tests isolated: the services under test commit freely,
    but those commits land on a savepoint inside this transaction and vanish
    when it unwinds. No test can see or corrupt another's data.
    """
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            if transaction.is_active:
                await transaction.rollback()


@pytest.fixture
async def db_session(connection):
    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        autoflush=False,
        # Service-level commits become savepoint releases rather than real
        # commits, which is what makes the rollback above total.
        join_transaction_mode="create_savepoint",
    )
    async with factory() as session:
        yield session


@pytest.fixture
async def committed_session(database):
    """A session that really commits, for tests about concurrency.

    Row locking and unique-constraint races cannot be observed inside a single
    transaction, so these tests need genuine, separate connections. They are
    responsible for cleaning up what they create; every row they insert is
    namespaced by a random suffix so a failed cleanup cannot collide with a
    later run.
    """
    from sqlalchemy.pool import NullPool

    created = create_async_engine(database, poolclass=NullPool, future=True)
    factory = async_sessionmaker(bind=created, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await created.dispose()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(db_session):
    """An ASGI client wired to the isolated test session.

    Requests go through the real router, dependency graph and exception
    handlers — only the database session is substituted.
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_db_session
    from app.main import app

    async def override():
        yield db_session

    app.dependency_overrides[get_db_session] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


@pytest.fixture
def unique() -> str:
    """A short random suffix for codes, emails and other unique columns."""
    return uuid.uuid4().hex[:8]


@pytest.fixture
def app_settings():
    """The live settings object. Mutate through ``monkeypatch.setattr``."""
    return settings
