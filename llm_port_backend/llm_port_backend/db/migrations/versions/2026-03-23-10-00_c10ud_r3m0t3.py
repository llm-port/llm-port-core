"""Add 'cloud' to provider_type and 'remote' to model_source enums.

Revision ID: c10ud_r3m0t3
Revises: n0d3c0ntr0l1
Create Date: 2026-03-23 10:00:00.000000
"""

from alembic import op

revision = "c10ud_r3m0t3"
down_revision = "n0d3c0ntr0l1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE provider_type ADD VALUE IF NOT EXISTS 'cloud'")
    op.execute("ALTER TYPE model_source ADD VALUE IF NOT EXISTS 'remote'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from enum types.
    # A full migration would require creating a new type, migrating
    # data, and swapping — omitted for brevity.  The extra values are
    # harmless if the downgrade is run.
    pass
