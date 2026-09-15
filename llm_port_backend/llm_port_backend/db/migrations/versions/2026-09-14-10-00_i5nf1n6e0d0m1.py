"""Add the neutral inference domain tables (Ray-first, Phase 1).

Creates:
    inference_control_planes
    inference_environments
    inference_environment_bindings
    inference_environment_nodes
    inference_deployments
    inference_endpoints
    model_availability

Legacy ``llm_runtimes`` / ``infra_node_workload_assignment`` are untouched:
the native single-node path continues to operate until it is explicitly
retired in a later migration phase.

Revision ID: i5nf1n6e0d0m1
Revises: us3rpr3f0001
Create Date: 2026-09-14 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision = "i5nf1n6e0d0m1"
down_revision = "us3rpr3f0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the inference domain tables."""
    op.create_table(
        "inference_control_planes",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("driver", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "connected", "disconnected", "degraded", "failed",
                "disabled",
                name="inference_control_plane_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "config_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "observed_status_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("credential_ref", sa.String(length=256), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "observed_generation", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("status_message", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inference_control_planes_name",
        "inference_control_planes",
        ["name"],
        unique=True,
    )
    op.create_index(
        "ix_inference_control_planes_driver",
        "inference_control_planes",
        ["driver"],
    )
    op.create_index(
        "ix_inference_control_planes_status",
        "inference_control_planes",
        ["status"],
    )

    op.create_table(
        "inference_environments",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "control_plane_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inference_control_planes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "preparing", "running", "ready", "degraded", "failed",
                "stopped",
                name="inference_environment_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "desired_state",
            sa.Enum(
                "running", "stopped", "deleted",
                name="inference_environment_desired_state",
            ),
            nullable=False,
            server_default="running",
        ),
        sa.Column("ray_version", sa.String(length=64), nullable=True),
        sa.Column(
            "head_node_id",
            UUID(as_uuid=True),
            sa.ForeignKey("infra_node.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column(
            "config_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "capabilities_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "observed_status_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "observed_generation", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("status_message", sa.Text(), nullable=True),
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
    op.create_index(
        "ix_inference_environments_control_plane_id",
        "inference_environments",
        ["control_plane_id"],
    )
    op.create_index(
        "ix_inference_environments_name",
        "inference_environments",
        ["name"],
        unique=True,
    )
    op.create_index(
        "ix_inference_environments_status",
        "inference_environments",
        ["status"],
    )
    op.create_index(
        "ix_inference_environments_head_node_id",
        "inference_environments",
        ["head_node_id"],
    )

    op.create_table(
        "inference_environment_bindings",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "environment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inference_environments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "control_plane_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inference_control_planes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("driver", sa.String(length=64), nullable=False),
        sa.Column(
            "roles_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "config_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "environment_id", "driver", name="uq_env_binding_env_driver"
        ),
    )
    op.create_index(
        "ix_inference_environment_bindings_environment_id",
        "inference_environment_bindings",
        ["environment_id"],
    )
    op.create_index(
        "ix_inference_environment_bindings_control_plane_id",
        "inference_environment_bindings",
        ["control_plane_id"],
    )

    op.create_table(
        "inference_environment_nodes",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "environment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inference_environments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            UUID(as_uuid=True),
            sa.ForeignKey("infra_node.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Enum("head", "worker", name="inference_environment_node_role"),
            nullable=False,
            server_default="worker",
        ),
        sa.Column("ray_status", sa.String(length=64), nullable=True),
        sa.Column(
            "observed_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "environment_id", "node_id", name="uq_env_node_env_node"
        ),
    )
    op.create_index(
        "ix_inference_environment_nodes_environment_id",
        "inference_environment_nodes",
        ["environment_id"],
    )
    op.create_index(
        "ix_inference_environment_nodes_node_id",
        "inference_environment_nodes",
        ["node_id"],
    )

    op.create_table(
        "inference_deployments",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "environment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inference_environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("llm_models.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "spec_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "desired_state",
            sa.Enum(
                "active", "stopped", "deleted",
                name="inference_deployment_desired_state",
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "phase",
            sa.Enum(
                "pending", "preparing", "applying", "running", "degraded",
                "stopped", "failed", "deleted",
                name="inference_deployment_phase",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "observed_generation", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "observed_status_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("phase_message", sa.Text(), nullable=True),
        sa.Column(
            "ready_replicas", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_replicas", sa.Integer(), nullable=False, server_default="0"
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
    op.create_index(
        "ix_inference_deployments_environment_id",
        "inference_deployments",
        ["environment_id"],
    )
    op.create_index(
        "ix_inference_deployments_model_id",
        "inference_deployments",
        ["model_id"],
    )
    op.create_index(
        "ix_inference_deployments_name",
        "inference_deployments",
        ["name"],
        unique=True,
    )
    op.create_index(
        "ix_inference_deployments_desired_state",
        "inference_deployments",
        ["desired_state"],
    )
    op.create_index(
        "ix_inference_deployments_phase",
        "inference_deployments",
        ["phase"],
    )

    op.create_table(
        "inference_endpoints",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deployment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inference_deployments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "path", sa.String(length=256), nullable=False, server_default="/v1"
        ),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "publishing", "published", "unpublishing", "degraded",
                "failed", "retired",
                name="inference_endpoint_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column(
            "published_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        sa.UniqueConstraint(
            "deployment_id", "name", name="uq_endpoint_deploy_name"
        ),
    )
    op.create_index(
        "ix_inference_endpoints_deployment_id",
        "inference_endpoints",
        ["deployment_id"],
    )
    op.create_index(
        "ix_inference_endpoints_status",
        "inference_endpoints",
        ["status"],
    )

    op.create_table(
        "model_availability",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("llm_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            UUID(as_uuid=True),
            sa.ForeignKey("infra_node.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "unknown", "syncing", "ready", "missing", "failed",
                name="model_availability_status",
            ),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "source_kind",
            sa.Enum(
                "synced", "pre_existing", "remote",
                name="model_availability_source_kind",
            ),
            nullable=False,
            server_default="synced",
        ),
        sa.Column("revision", sa.String(length=256), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=128), nullable=True),
        sa.Column("root_path", sa.Text(), nullable=True),
        sa.Column(
            "size_bytes", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_id", "node_id", name="uq_model_availability_model_node"
        ),
    )
    op.create_index(
        "ix_model_availability_model_id", "model_availability", ["model_id"]
    )
    op.create_index(
        "ix_model_availability_node_id", "model_availability", ["node_id"]
    )
    op.create_index(
        "ix_model_availability_status", "model_availability", ["status"]
    )


def downgrade() -> None:
    """Drop only the resources introduced by this revision."""
    op.drop_index("ix_model_availability_status", table_name="model_availability")
    op.drop_index("ix_model_availability_node_id", table_name="model_availability")
    op.drop_index("ix_model_availability_model_id", table_name="model_availability")
    op.drop_table("model_availability")

    op.drop_index("ix_inference_endpoints_status", table_name="inference_endpoints")
    op.drop_index(
        "ix_inference_endpoints_deployment_id", table_name="inference_endpoints"
    )
    op.drop_table("inference_endpoints")

    op.drop_index(
        "ix_inference_deployments_phase", table_name="inference_deployments"
    )
    op.drop_index(
        "ix_inference_deployments_desired_state", table_name="inference_deployments"
    )
    op.drop_index(
        "ix_inference_deployments_name", table_name="inference_deployments"
    )
    op.drop_index(
        "ix_inference_deployments_model_id", table_name="inference_deployments"
    )
    op.drop_index(
        "ix_inference_deployments_environment_id", table_name="inference_deployments"
    )
    op.drop_table("inference_deployments")

    op.drop_index(
        "ix_inference_environment_nodes_node_id",
        table_name="inference_environment_nodes",
    )
    op.drop_index(
        "ix_inference_environment_nodes_environment_id",
        table_name="inference_environment_nodes",
    )
    op.drop_table("inference_environment_nodes")

    op.drop_index(
        "ix_inference_environment_bindings_control_plane_id",
        table_name="inference_environment_bindings",
    )
    op.drop_index(
        "ix_inference_environment_bindings_environment_id",
        table_name="inference_environment_bindings",
    )
    op.drop_table("inference_environment_bindings")

    op.drop_index(
        "ix_inference_environments_head_node_id", table_name="inference_environments"
    )
    op.drop_index(
        "ix_inference_environments_status", table_name="inference_environments"
    )
    op.drop_index("ix_inference_environments_name", table_name="inference_environments")
    op.drop_index(
        "ix_inference_environments_control_plane_id", table_name="inference_environments"
    )
    op.drop_table("inference_environments")

    op.drop_index(
        "ix_inference_control_planes_status", table_name="inference_control_planes"
    )
    op.drop_index(
        "ix_inference_control_planes_driver", table_name="inference_control_planes"
    )
    op.drop_index(
        "ix_inference_control_planes_name", table_name="inference_control_planes"
    )
    op.drop_table("inference_control_planes")

    # Drop enum types created by this revision.
    for enum_name in (
        "model_availability_source_kind",
        "model_availability_status",
        "inference_endpoint_status",
        "inference_deployment_phase",
        "inference_deployment_desired_state",
        "inference_environment_node_role",
        "inference_environment_desired_state",
        "inference_environment_status",
        "inference_control_plane_status",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
