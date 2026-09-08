"""add partial indexes for active rows

Revision ID: 550bd006b4c4
Revises: 72f40f9e767d
Create Date: 2026-09-07 16:07:49.704490
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '550bd006b4c4'
down_revision: str | None = '72f40f9e767d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table schema, table, index name, columns)
ACTIVE_INDEXES = [
    ("identity", "users", "ix_users_active_role", "role, status"),
    ("warehouses", "warehouses", "ix_warehouses_active_city", "city, status"),
    ("warehouses", "warehouse_zones", "ix_zones_active_warehouse", "warehouse_id"),
    ("catalog", "skus", "ix_skus_active_category", "category"),
    ("couriers", "couriers", "ix_couriers_active_city", "home_city, availability_status"),
    ("shipments", "shipments", "ix_shipments_active_status", "status, created_at"),
]


def upgrade() -> None:
    """Partial indexes covering only live rows.

    Declared on the models too, so ``alembic check`` sees model and database
    agree; this migration is what creates them on an existing database.

    Soft deletion means deleted rows stay in the table. Rather than moving them
    out, every hot query filters ``deleted_at IS NULL``, so an index restricted
    to that predicate stays the size of the live data no matter how much history
    accumulates. This is the cheap half of the archival strategy (ADR-016).
    """
    for schema, table, name, columns in ACTIVE_INDEXES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON {schema}.{table} ({columns}) "
            "WHERE deleted_at IS NULL"
        )


def downgrade() -> None:
    for schema, _table, name, _columns in ACTIVE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {schema}.{name}")
