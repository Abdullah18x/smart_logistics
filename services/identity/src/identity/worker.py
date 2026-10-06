"""Identity background process: ``python -m identity.worker``.

Publishes the outbox and consumes ``warehouse.deleted`` to drop operator scopes.
"""

import asyncio

from identity.config import settings
from identity.db import database
from identity.events import build_processor
from sl_platform.worker import run_worker


def main() -> None:
    asyncio.run(run_worker(settings, database, [build_processor(database.sessionmaker)]))


if __name__ == "__main__":
    main()
