"""Add pipeline observability columns and tool call log table.

Adds structured observability to ``llm_gateway_request_log``:
  - session_id, finish_reason, retry_count
  - skills_used (JSONB), rag_context (JSONB)
  - mcp_tool_call_count, mcp_tool_loop_iterations

Creates ``llm_tool_call_log`` child table for per-tool-call telemetry.

Revision ID: 1e2f3a4b5c6d
Revises: 0d1e2f3a4b5c
Create Date: 2026-04-04 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON as PGJSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID

# revision identifiers, used by Alembic.
revision = "1e2f3a4b5c6d"
down_revision = "0d1e2f3a4b5c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- New columns on llm_gateway_request_log --
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("session_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("finish_reason", sa.String(32), nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("retry_count", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("skills_used", PGJSON(), nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("rag_context", PGJSON(), nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("mcp_tool_call_count", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("mcp_tool_loop_iterations", sa.Integer(), nullable=True, server_default="0"),
    )

    op.create_index(
        "ix_llm_gateway_request_log_session_id",
        "llm_gateway_request_log",
        ["session_id"],
    )

    # -- Tool call log table --
    op.create_table(
        "llm_tool_call_log",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "request_log_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("llm_gateway_request_log.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("request_id", sa.String(128), nullable=False, index=True),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column("mcp_server", sa.String(128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("llm_tool_call_log")
    op.drop_index("ix_llm_gateway_request_log_session_id", table_name="llm_gateway_request_log")
    op.drop_column("llm_gateway_request_log", "mcp_tool_loop_iterations")
    op.drop_column("llm_gateway_request_log", "mcp_tool_call_count")
    op.drop_column("llm_gateway_request_log", "rag_context")
    op.drop_column("llm_gateway_request_log", "skills_used")
    op.drop_column("llm_gateway_request_log", "retry_count")
    op.drop_column("llm_gateway_request_log", "finish_reason")
    op.drop_column("llm_gateway_request_log", "session_id")
