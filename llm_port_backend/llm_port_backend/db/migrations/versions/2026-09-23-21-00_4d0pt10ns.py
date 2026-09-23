"""vLLM containers LLM.Port found on a machine and took over.

One table, ``inference_adoptions``: which machine and container, the name
clients call it by, what it serves, the provider row routing it, and -- when
it is moved into a cluster -- the deployment that replaced it and where the
move stands. Not columns on the deployment: the reconciler rewrites that
row's observed state every pass, and a second writer there would lose
updates. Foreign keys are SET NULL so the record outlives the machine, the
provider and the deployment.

Revision ID: 4d0pt10ns
Revises: d3r1v3dprov
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "4d0pt10ns"
down_revision = "d3r1v3dprov"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inference_adoptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("infra_node.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("container_name", sa.String(length=256), nullable=False),
        sa.Column("alias", sa.String(length=256), nullable=False),
        sa.Column("served_model_name", sa.String(length=512), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=True),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("llm_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inference_deployments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="routed"),
        sa.Column("detail_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("routed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("switched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("switched_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_inference_adoptions_node_id", "inference_adoptions", ["node_id"])
    op.create_index("ix_inference_adoptions_alias", "inference_adoptions", ["alias"])
    op.create_index("ix_inference_adoptions_deployment_id", "inference_adoptions", ["deployment_id"])
    op.create_index("ix_inference_adoptions_state", "inference_adoptions", ["state"])


def downgrade() -> None:
    for name in ("state", "deployment_id", "alias", "node_id"):
        op.drop_index(f"ix_inference_adoptions_{name}", table_name="inference_adoptions")
    op.drop_table("inference_adoptions")
