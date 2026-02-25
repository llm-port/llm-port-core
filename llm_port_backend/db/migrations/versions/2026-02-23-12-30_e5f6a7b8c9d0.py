"""Add rag container grants table.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-02-23 12-30-00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create rag_container_grants table."""
    op.create_table(
        "rag_container_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=256), nullable=False),
        sa.Column("workspace_id", sa.String(length=256), nullable=True),
        sa.Column("container_id", sa.String(length=64), nullable=False),
        sa.Column("actions", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rag_container_grants_user", "rag_container_grants", ["user_id"], unique=False)
    op.create_index(
        "ix_rag_container_grants_scope",
        "rag_container_grants",
        ["tenant_id", "workspace_id", "container_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop rag_container_grants table."""
    op.drop_index("ix_rag_container_grants_scope", table_name="rag_container_grants")
    op.drop_index("ix_rag_container_grants_user", table_name="rag_container_grants")
    op.drop_table("rag_container_grants")
