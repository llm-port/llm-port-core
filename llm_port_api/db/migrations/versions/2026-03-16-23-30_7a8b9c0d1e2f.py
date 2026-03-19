"""Add node metadata columns to llm_provider_instance.

Revision ID: 7a8b9c0d1e2f
Revises: 6f7a8b9c0d1e
Create Date: 2026-03-16 23:30:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "7a8b9c0d1e2f"
down_revision = "6f7a8b9c0d1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_provider_instance",
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "llm_provider_instance",
        sa.Column("node_metadata", sa.JSON(), nullable=True),
    )
    op.add_column(
        "llm_provider_instance",
        sa.Column("capacity_hints", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_llm_provider_instance_node_id",
        "llm_provider_instance",
        ["node_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_llm_provider_instance_node_id", table_name="llm_provider_instance")
    op.drop_column("llm_provider_instance", "capacity_hints")
    op.drop_column("llm_provider_instance", "node_metadata")
    op.drop_column("llm_provider_instance", "node_id")
