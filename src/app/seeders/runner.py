"""Seed the database with development data.

Usage:
    python -m app.seeders.runner              # all seeders
    python -m app.seeders.runner --only users # a single seeder

Seeders are idempotent, so this is safe to re-run. Refused in production.
"""

import argparse
import asyncio
import logging
import sys

from app.core.config import settings
from app.core.database import engine, session_scope
from app.seeders.registry import SEEDERS

logger = logging.getLogger("seeder")


async def seed(only: str | None = None) -> int:
    if settings.is_production:
        raise RuntimeError("Refusing to seed a production database.")

    selected = [s for s in SEEDERS if only is None or s.name == only]
    if only and not selected:
        available = ", ".join(s.name for s in SEEDERS)
        raise SystemExit(f"Unknown seeder '{only}'. Available: {available}")

    total = 0
    # One transaction for the whole run: a partial seed is worse than none.
    async with session_scope() as session:
        for seeder in selected:
            created = await seeder.run(session)
            total += created
            logger.info("%-14s created %d row(s)", seeder.name, created)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate the database with dummy data.")
    parser.add_argument("--only", help="Run a single seeder by name.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
    )

    async def _run() -> int:
        try:
            return await seed(args.only)
        finally:
            await engine.dispose()

    try:
        total = asyncio.run(_run())
    except Exception as exc:
        logger.error("seeding failed: %s", exc)
        sys.exit(1)

    logger.info("done — %d row(s) created", total)
    if total:
        logger.info("default password: %s", settings.seed_default_password)


if __name__ == "__main__":
    main()
