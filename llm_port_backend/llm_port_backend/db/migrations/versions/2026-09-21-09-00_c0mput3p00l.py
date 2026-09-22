"""Compute pools, and vendor-neutral names in the neutral inference domain.

Two changes that get more expensive the longer they wait:

1. ``ray_version`` / ``ray_status`` were one backend's vocabulary on models
   every backend shares.  Renamed to ``runtime_version`` / ``member_status``.
   The Ray driver keeps its own Ray-named internals -- that is correct; what
   was wrong is the neutral domain carrying them.

2. ``inference_compute_pools``: the persisted equivalence class of nodes under
   the same compatibility rules a runtime bundle is matched with.  Without it
   a cluster is the only grouping, so a mixed-vendor cluster cannot say which
   machines are interchangeable -- which is what blocks AMD, Intel or an
   Apple/exo group joining later.

Revision ID: c0mput3p00l
Revises: m0d3lav41l
Create Date: 2026-09-21 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c0mput3p00l"
down_revision = "m0d3lav41l"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "inference_environments", "ray_version", new_column_name="runtime_version"
    )
    op.alter_column(
        "inference_environment_nodes", "ray_status", new_column_name="member_status"
    )

    op.create_table(
        "inference_compute_pools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inference_environments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("signature", sa.String(length=256), nullable=False),
        sa.Column("accelerator_vendor", sa.String(length=64), nullable=False),
        sa.Column("accelerator_family", sa.String(length=64), nullable=True),
        sa.Column("cpu_architecture", sa.String(length=32), nullable=False),
        sa.Column(
            "labels_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "managed", sa.Boolean(), nullable=False, server_default=sa.text("false")
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
        sa.UniqueConstraint("environment_id", "name", name="uq_compute_pool_env_name"),
    )
    op.create_index(
        "ix_inference_compute_pools_environment_id",
        "inference_compute_pools",
        ["environment_id"],
    )
    op.create_index(
        "ix_inference_compute_pools_signature",
        "inference_compute_pools",
        ["signature"],
    )

    op.add_column(
        "inference_environment_nodes",
        sa.Column(
            "compute_pool_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inference_compute_pools.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_inference_environment_nodes_compute_pool_id",
        "inference_environment_nodes",
        ["compute_pool_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inference_environment_nodes_compute_pool_id",
        table_name="inference_environment_nodes",
    )
    op.drop_column("inference_environment_nodes", "compute_pool_id")
    op.drop_index(
        "ix_inference_compute_pools_signature", table_name="inference_compute_pools"
    )
    op.drop_index(
        "ix_inference_compute_pools_environment_id",
        table_name="inference_compute_pools",
    )
    op.drop_table("inference_compute_pools")
    op.alter_column(
        "inference_environment_nodes", "member_status", new_column_name="ray_status"
    )
    op.alter_column(
        "inference_environments", "runtime_version", new_column_name="ray_version"
    )
