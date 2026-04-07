"""Add realm and source columns to mcp_tools.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-07 10:00:00

"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_tools",
        sa.Column("realm", sa.String(32), nullable=False, server_default="mcp_remote"),
    )
    op.add_column(
        "mcp_tools",
        sa.Column("source", sa.String(32), nullable=False, server_default="mcp"),
    )


def downgrade() -> None:
    op.drop_column("mcp_tools", "source")
    op.drop_column("mcp_tools", "realm")
