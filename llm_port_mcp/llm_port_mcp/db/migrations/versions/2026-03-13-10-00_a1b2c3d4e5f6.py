"""Create mcp_servers and mcp_tools tables.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-03-13 10:00:00

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.create_table(
        "mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(128),
            nullable=False,
            server_default="default",
            index=True,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("command_json", postgresql.JSONB(), nullable=True),
        sa.Column("args_json", postgresql.JSONB(), nullable=True),
        sa.Column("working_dir", sa.String(1024), nullable=True),
        sa.Column("headers_json_encrypted", sa.Text(), nullable=True),
        sa.Column("env_json_encrypted", sa.Text(), nullable=True),
        sa.Column("tool_prefix", sa.String(128), nullable=False),
        sa.Column(
            "pii_mode", sa.String(32), nullable=False, server_default="redact"
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="registering"
        ),
        sa.Column("timeout_sec", sa.Integer(), nullable=False, server_default="60"),
        sa.Column(
            "heartbeat_interval_sec",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_discovery_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_mcp_server_tenant_name"),
        sa.UniqueConstraint(
            "tenant_id", "tool_prefix", name="uq_mcp_server_tenant_prefix"
        ),
    )

    op.create_table(
        "mcp_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "tenant_id",
            sa.String(128),
            nullable=False,
            server_default="default",
            index=True,
        ),
        sa.Column("upstream_name", sa.String(256), nullable=False),
        sa.Column("qualified_name", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_schema_json", postgresql.JSONB(), nullable=True),
        sa.Column("openai_schema_json", postgresql.JSONB(), nullable=True),
        sa.Column("annotations_json", postgresql.JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("version", sa.String(32), nullable=False, server_default="1"),
        sa.Column("schema_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "qualified_name", name="uq_mcp_tool_tenant_qualified"
        ),
    )


def downgrade() -> None:
    """Undo the migration."""
    op.drop_table("mcp_tools")
    op.drop_table("mcp_servers")
