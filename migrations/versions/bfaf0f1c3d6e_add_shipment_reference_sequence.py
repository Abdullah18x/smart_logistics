"""add shipment reference sequence

Revision ID: bfaf0f1c3d6e
Revises: 824ac2610207
Create Date: 2026-09-06 18:57:00.355140
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'bfaf0f1c3d6e'
down_revision: str | None = '824ac2610207'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A sequence, not MAX()+1: concurrent shipment creation must not collide.
    # Starts above the seeded references (SL-2026-000001..7).
    op.execute("CREATE SEQUENCE IF NOT EXISTS shipments.shipment_reference_seq START 1000")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS shipments.shipment_reference_seq")
