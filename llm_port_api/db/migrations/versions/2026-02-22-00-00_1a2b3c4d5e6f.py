"""Create gateway control tables.

Revision ID: 1a2b3c4d5e6f
Revises:
Create Date: 2026-02-22 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "1a2b3c4d5e6f"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply schema changes."""
    op.create_table(
        "llm_model_alias",
        sa.Column("alias", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "default_parameters", postgresql.JSON(astext_type=sa.Text()), nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.PrimaryKeyConstraint("alias"),
    )

    op.create_table(
        "llm_provider_instance",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "vllm",
                "llamacpp",
                "tgi",
                "ollama",
                "remote_openai",
                name="gateway_provider_type",
            ),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "capabilities", postgresql.JSON(astext_type=sa.Text()), nullable=True,
        ),
        sa.Column(
            "health_status",
            sa.Enum(
                "healthy",
                "unhealthy",
                "unknown",
                name="gateway_provider_health_status",
            ),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "llm_pool_membership",
        sa.Column("model_alias", sa.String(length=256), nullable=False),
        sa.Column(
            "provider_instance_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column("weight_override", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(
            ["model_alias"], ["llm_model_alias.alias"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_instance_id"], ["llm_provider_instance.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("model_alias", "provider_instance_id"),
    )

    op.create_table(
        "tenant_llm_policy",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column(
            "privacy_mode",
            sa.Enum("full", "redacted", "metadata_only", name="gateway_privacy_mode"),
            nullable=False,
            server_default="metadata_only",
        ),
        sa.Column(
            "allowed_model_aliases",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "allowed_provider_types",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("rpm_limit", sa.Integer(), nullable=True),
        sa.Column("tpm_limit", sa.Integer(), nullable=True),
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
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    op.create_table(
        "llm_gateway_request_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("model_alias", sa.String(length=256), nullable=True),
        sa.Column("provider_instance_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ttft_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_gateway_request_log_request_id",
        "llm_gateway_request_log",
        ["request_id"],
    )
    op.create_index(
        "ix_llm_gateway_request_log_tenant_id", "llm_gateway_request_log", ["tenant_id"],
    )
    op.create_index(
        "ix_llm_gateway_request_log_user_id", "llm_gateway_request_log", ["user_id"],
    )
    op.create_index(
        "ix_llm_gateway_request_log_provider_instance_id",
        "llm_gateway_request_log",
        ["provider_instance_id"],
    )
    op.create_index(
        "ix_llm_gateway_request_log_created_at",
        "llm_gateway_request_log",
        ["created_at"],
    )


def downgrade() -> None:
    """Rollback schema changes."""
    op.drop_index(
        "ix_llm_gateway_request_log_created_at", table_name="llm_gateway_request_log",
    )
    op.drop_index(
        "ix_llm_gateway_request_log_provider_instance_id",
        table_name="llm_gateway_request_log",
    )
    op.drop_index(
        "ix_llm_gateway_request_log_user_id", table_name="llm_gateway_request_log",
    )
    op.drop_index(
        "ix_llm_gateway_request_log_tenant_id", table_name="llm_gateway_request_log",
    )
    op.drop_index(
        "ix_llm_gateway_request_log_request_id", table_name="llm_gateway_request_log",
    )
    op.drop_table("llm_gateway_request_log")
    op.drop_table("tenant_llm_policy")
    op.drop_table("llm_pool_membership")
    op.drop_table("llm_provider_instance")
    op.drop_table("llm_model_alias")

    for enum_name in (
        "gateway_privacy_mode",
        "gateway_provider_health_status",
        "gateway_provider_type",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
