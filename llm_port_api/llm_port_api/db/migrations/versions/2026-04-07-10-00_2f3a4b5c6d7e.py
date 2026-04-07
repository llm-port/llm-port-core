"""Add session execution policy, tool overrides, and client capability tables.

Introduces the tool routing data model:
  - ``session_execution_mode`` enum type
  - ``tool_realm`` enum type
  - ``execution_mode`` column on ``chat_session``
  - ``session_execution_policy`` table
  - ``session_tool_override`` table
  - ``session_client_capability`` table

Revision ID: 2f3a4b5c6d7e
Revises: 1e2f3a4b5c6d
Create Date: 2026-04-07 10:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "2f3a4b5c6d7e"
down_revision = "1e2f3a4b5c6d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use raw SQL throughout to avoid sa.Enum auto-creating PG enum types
    # via CreateEnumType DDL events (create_type=False is unreliable with asyncpg).

    # -- Enum types --
    op.execute(
        "CREATE TYPE session_execution_mode"
        " AS ENUM ('local_only', 'server_only', 'hybrid')"
    )
    op.execute(
        "CREATE TYPE tool_realm"
        " AS ENUM ('server_managed', 'mcp_remote', 'client_local', 'client_proxied')"
    )

    # -- Add execution_mode column to chat_session --
    op.execute(
        "ALTER TABLE chat_session"
        " ADD COLUMN execution_mode session_execution_mode"
        " NOT NULL DEFAULT 'server_only'"
    )

    # -- session_execution_policy --
    op.execute(
        "CREATE TABLE session_execution_policy ("
        "  session_id UUID PRIMARY KEY REFERENCES chat_session(id) ON DELETE CASCADE,"
        "  execution_mode session_execution_mode NOT NULL DEFAULT 'server_only',"
        "  hybrid_preference VARCHAR(32),"
        "  catalog_version INTEGER NOT NULL DEFAULT 0,"
        "  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )

    # -- session_tool_override --
    op.execute(
        "CREATE TABLE session_tool_override ("
        "  session_id UUID NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,"
        "  tool_id VARCHAR(512) NOT NULL,"
        "  enabled BOOLEAN NOT NULL DEFAULT true,"
        "  updated_by VARCHAR(128),"
        "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        "  PRIMARY KEY (session_id, tool_id)"
        ")"
    )

    # -- session_client_capability --
    op.execute(
        "CREATE TABLE session_client_capability ("
        "  session_id UUID NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,"
        "  client_id VARCHAR(256) NOT NULL,"
        "  tool_id VARCHAR(512) NOT NULL,"
        "  realm tool_realm NOT NULL DEFAULT 'client_local',"
        "  schema_json JSONB,"
        "  available BOOLEAN NOT NULL DEFAULT true,"
        "  last_seen_at TIMESTAMPTZ,"
        "  PRIMARY KEY (session_id, client_id, tool_id)"
        ")"
    )

    op.execute(
        "CREATE INDEX ix_session_client_capability_session"
        " ON session_client_capability (session_id)"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_session_client_capability_session",
        table_name="session_client_capability",
    )
    op.drop_table("session_client_capability")
    op.drop_table("session_tool_override")
    op.drop_table("session_execution_policy")
    op.drop_column("chat_session", "execution_mode")
    op.execute("DROP TYPE IF EXISTS tool_realm")
    op.execute("DROP TYPE IF EXISTS session_execution_mode")
