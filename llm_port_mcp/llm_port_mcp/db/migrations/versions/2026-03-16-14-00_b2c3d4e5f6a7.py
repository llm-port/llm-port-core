"""Add settings_schema_json and provider_settings_json to mcp_servers.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-16 14:00:00

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column("settings_schema_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("provider_settings_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "provider_settings_json")
    op.drop_column("mcp_servers", "settings_schema_json")
