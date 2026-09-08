"""Development data seeders."""

from app.seeders.base import Seeder, seed_id
from app.seeders.registry import SEEDERS

__all__ = ["SEEDERS", "Seeder", "seed_id"]
