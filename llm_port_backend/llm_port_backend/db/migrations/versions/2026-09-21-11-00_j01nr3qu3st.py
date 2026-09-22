"""Join requests: let a machine ask, and a human approve.

The enrollment-token direction only works when the operator's browser and a
shell on the new machine share a clipboard.  This table carries the other
direction, where nothing long is ever typed.

Revision ID: j01nr3qu3st
Revises: c0mput3p00l
Create Date: 2026-09-21 11:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "j01nr3qu3st"
down_revision: Union[str, None] = "c0mput3p00l"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "infra_node_join_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("poll_secret_hash", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column(
            "capabilities_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["decided_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["node_id"], ["infra_node.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_infra_node_join_request_code", "infra_node_join_request", ["code"])
    op.create_index("ix_infra_node_join_request_agent_id", "infra_node_join_request", ["agent_id"])
    # The pending queue is what both the operator's list and the rate limiter
    # read, and it is the only hot path on this table.
    op.create_index(
        "ix_infra_node_join_request_pending",
        "infra_node_join_request",
        ["status", "expires_at"],
    )
    # A live code must be unambiguous: if two pending requests shared one, the
    # operator comparing codes could approve the wrong machine.
    op.execute(
        "CREATE UNIQUE INDEX uq_infra_node_join_request_live_code "
        "ON infra_node_join_request (code) WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_infra_node_join_request_live_code")
    op.drop_index("ix_infra_node_join_request_pending", table_name="infra_node_join_request")
    op.drop_index("ix_infra_node_join_request_agent_id", table_name="infra_node_join_request")
    op.drop_index("ix_infra_node_join_request_code", table_name="infra_node_join_request")
    op.drop_table("infra_node_join_request")
