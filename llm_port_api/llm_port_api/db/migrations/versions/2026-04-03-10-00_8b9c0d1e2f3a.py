"""Add cost estimate columns to llm_gateway_request_log.

Revision ID: 8b9c0d1e2f3a
Revises: 7a8b9c0d1e2f
Create Date: 2026-04-03 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "8b9c0d1e2f3a"
down_revision = "7a8b9c0d1e2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("estimated_input_cost", sa.Numeric(18, 10), nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("estimated_output_cost", sa.Numeric(18, 10), nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("estimated_total_cost", sa.Numeric(18, 10), nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("currency", sa.String(3), server_default="USD", nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column(
            "price_catalog_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("cost_estimate_status", sa.String(16), nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_gateway_request_log",
        sa.Column("stream", sa.Boolean(), nullable=True),
    )
    # Composite indexes for dashboard aggregation queries
    op.create_index(
        "ix_request_log_provider_created",
        "llm_gateway_request_log",
        ["provider_instance_id", "created_at"],
    )
    op.create_index(
        "ix_request_log_model_created",
        "llm_gateway_request_log",
        ["model_alias", "created_at"],
    )
    op.create_index(
        "ix_request_log_user_created",
        "llm_gateway_request_log",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_request_log_status_created",
        "llm_gateway_request_log",
        ["status_code", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_request_log_status_created", table_name="llm_gateway_request_log")
    op.drop_index("ix_request_log_user_created", table_name="llm_gateway_request_log")
    op.drop_index("ix_request_log_model_created", table_name="llm_gateway_request_log")
    op.drop_index("ix_request_log_provider_created", table_name="llm_gateway_request_log")
    op.drop_column("llm_gateway_request_log", "stream")
    op.drop_column("llm_gateway_request_log", "cached_tokens")
    op.drop_column("llm_gateway_request_log", "cost_estimate_status")
    op.drop_column("llm_gateway_request_log", "price_catalog_id")
    op.drop_column("llm_gateway_request_log", "currency")
    op.drop_column("llm_gateway_request_log", "estimated_total_cost")
    op.drop_column("llm_gateway_request_log", "estimated_output_cost")
    op.drop_column("llm_gateway_request_log", "estimated_input_cost")
