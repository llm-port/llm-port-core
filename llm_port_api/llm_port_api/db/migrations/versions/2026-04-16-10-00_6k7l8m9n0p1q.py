"""Add source_kind and source_id columns to llm_provider_instance.

Revision ID: 6k7l8m9n0p1q
Revises: 5j6k7l8m9n0p
Create Date: 2026-04-16 10:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "6k7l8m9n0p1q"
down_revision = "5j6k7l8m9n0p"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE llm_provider_instance "
        "ADD COLUMN IF NOT EXISTS source_kind VARCHAR(32)"
    )
    op.execute(
        "ALTER TABLE llm_provider_instance "
        "ADD COLUMN IF NOT EXISTS source_id UUID"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_provider_instance_source "
        "ON llm_provider_instance (source_kind, source_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_provider_instance_source "
        "ON llm_provider_instance (source_kind, source_id) "
        "WHERE source_kind IS NOT NULL AND source_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS uq_llm_provider_instance_source"
    )
    op.execute(
        "DROP INDEX IF EXISTS ix_llm_provider_instance_source"
    )
    op.execute(
        "ALTER TABLE llm_provider_instance DROP COLUMN IF EXISTS source_id"
    )
    op.execute(
        "ALTER TABLE llm_provider_instance DROP COLUMN IF EXISTS source_kind"
    )

