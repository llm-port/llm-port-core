"""Add infra_node_profile table and profile_id FK on infra_node.

Revision ID: n0d3pr0f1l31
Revises: st4tusm3ss4g
Create Date: 2026-03-26 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "n0d3pr0f1l31"
down_revision = "st4tusm3ss4g"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "infra_node_profile",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("runtime_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("gpu_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("storage_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("network_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("logging_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("security_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("update_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_infra_node_profile_name", "infra_node_profile", ["name"])

    op.add_column(
        "infra_node",
        sa.Column(
            "profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("infra_node_profile.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("infra_node", "profile_id")
    op.drop_index("ix_infra_node_profile_name", table_name="infra_node_profile")
    op.drop_table("infra_node_profile")
