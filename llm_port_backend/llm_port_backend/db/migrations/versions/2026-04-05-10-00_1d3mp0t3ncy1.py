"""Widen infra_node_command.idempotency_key to VARCHAR(256).

The retired-key suffix ``{key}::retired::{id}`` can exceed 128 chars
when the original key already contains two UUIDs (e.g. sync-profile keys).

Revision ID: 1d3mp0t3ncy1
Revises: n0d3pr0f1l31
Create Date: 2026-04-05 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "1d3mp0t3ncy1"
down_revision = "n0d3pr0f1l31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "infra_node_command",
        "idempotency_key",
        existing_type=sa.String(128),
        type_=sa.String(256),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "infra_node_command",
        "idempotency_key",
        existing_type=sa.String(256),
        type_=sa.String(128),
        existing_nullable=False,
    )
