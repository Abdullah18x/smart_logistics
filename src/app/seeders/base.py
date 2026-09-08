"""Seeder contract and shared helpers."""

import uuid
from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

# Namespace for deterministic ids, so seeded rows keep the same id across runs
# and across seeders that reference each other.
SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def seed_id(entity: str, key: str) -> uuid.UUID:
    """A stable id derived from an entity type and a natural key."""
    return uuid.uuid5(SEED_NAMESPACE, f"{entity}:{key}")


class Seeder(ABC):
    """One seeder per entity.

    Seeders must be idempotent: running them twice leaves the database in the
    same state as running them once.
    """

    name: str = "seeder"
    #: Seeders that must run before this one.
    depends_on: tuple[str, ...] = ()

    @abstractmethod
    async def run(self, session: AsyncSession) -> int:
        """Insert missing rows and return how many were created."""
        raise NotImplementedError
