"""Add RBAC and LLM server tables.

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-02-21 00-00-00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create RBAC + LLM tables."""

    # ------------------------------------------------------------------
    # RBAC tables
    # ------------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource", "action", name="uq_permission_resource_action"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )

    # ------------------------------------------------------------------
    # LLM tables
    # ------------------------------------------------------------------
    op.create_table(
        "llm_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "type",
            sa.Enum("vllm", "llamacpp", "tgi", "ollama", name="provider_type"),
            nullable=False,
        ),
        sa.Column(
            "target",
            sa.Enum("local_docker", name="provider_target"),
            nullable=False,
            server_default="local_docker",
        ),
        sa.Column("capabilities", postgresql.JSON(), nullable=True),
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
        "llm_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column(
            "source",
            sa.Enum("huggingface", "local_path", "archive_import", name="model_source"),
            nullable=False,
        ),
        sa.Column("hf_repo_id", sa.String(length=512), nullable=True),
        sa.Column("hf_revision", sa.String(length=256), nullable=True),
        sa.Column("license_ack_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tags", postgresql.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("available", "downloading", "failed", "deleting", name="model_status"),
            nullable=False,
            server_default="downloading",
        ),
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
    op.create_index("ix_llm_models_status", "llm_models", ["status"])

    op.create_table(
        "model_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "format",
            sa.Enum("safetensors", "gguf", "other", name="artifact_format"),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("engine_compat", postgresql.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["model_id"], ["llm_models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_artifacts_model_id", "model_artifacts", ["model_id"])

    op.create_table(
        "llm_runtimes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "creating", "starting", "running", "stopping", "stopped", "error",
                name="runtime_status",
            ),
            nullable=False,
            server_default="creating",
        ),
        sa.Column("endpoint_url", sa.String(length=1024), nullable=True),
        sa.Column("openai_compat", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("generic_config", postgresql.JSON(), nullable=True),
        sa.Column("provider_config", postgresql.JSON(), nullable=True),
        sa.Column("container_ref", sa.String(length=256), nullable=True),
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
        sa.ForeignKeyConstraint(["provider_id"], ["llm_providers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_id"], ["llm_models.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_runtimes_provider_id", "llm_runtimes", ["provider_id"])
    op.create_index("ix_llm_runtimes_model_id", "llm_runtimes", ["model_id"])
    op.create_index("ix_llm_runtimes_status", "llm_runtimes", ["status"])

    op.create_table(
        "download_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "running", "success", "failed", "canceled", name="download_job_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("log_ref", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["model_id"], ["llm_models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_download_jobs_model_id", "download_jobs", ["model_id"])
    op.create_index("ix_download_jobs_status", "download_jobs", ["status"])


def downgrade() -> None:
    """Drop LLM + RBAC tables."""
    # LLM tables (reverse order)
    op.drop_table("download_jobs")
    op.drop_table("llm_runtimes")
    op.drop_table("model_artifacts")
    op.drop_table("llm_models")
    op.drop_table("llm_providers")

    # RBAC tables (reverse order)
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")

    # Drop enum types
    for enum_name in (
        "download_job_status",
        "runtime_status",
        "artifact_format",
        "model_status",
        "model_source",
        "provider_target",
        "provider_type",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
