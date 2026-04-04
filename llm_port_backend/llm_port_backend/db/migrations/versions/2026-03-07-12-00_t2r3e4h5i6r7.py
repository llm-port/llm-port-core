"""Add hierarchical collections (parent_id) and document summary.

Revision ID: t2r3e4h5i6r7
Revises: r4g1l1t3v0c1
Create Date: 2026-03-07 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "t2r3e4h5i6r7"
down_revision = "r4g1l1t3v0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Collections: add parent_id for tree hierarchy --
    op.add_column(
        "rag_lite_collections",
        sa.Column(
            "parent_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rag_lite_collections.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_rag_lite_collections_parent_id",
        "rag_lite_collections",
        ["parent_id"],
    )

    # -- Documents: add summary field --
    op.add_column(
        "rag_lite_documents",
        sa.Column("summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rag_lite_documents", "summary")
    op.drop_index("ix_rag_lite_collections_parent_id", table_name="rag_lite_collections")
    op.drop_column("rag_lite_collections", "parent_id")
