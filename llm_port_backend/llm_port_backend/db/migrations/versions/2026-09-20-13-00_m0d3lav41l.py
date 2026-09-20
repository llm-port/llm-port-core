"""Add pending and stale states to model_availability_status enum.

Revision ID: m0d3lav41l
Revises: i5nf1n6e0d0m1
Create Date: 2026-09-20 13:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "m0d3lav41l"
down_revision = "i5nf1n6e0d0m1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add pending and stale values to model_availability_status enum."""
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE model_availability_status ADD VALUE IF NOT EXISTS 'pending'"
        )
        op.execute(
            "ALTER TYPE model_availability_status ADD VALUE IF NOT EXISTS 'stale'"
        )


def downgrade() -> None:
    """Downgrade is a no-op: PostgreSQL does not support removing enum values."""
    pass

