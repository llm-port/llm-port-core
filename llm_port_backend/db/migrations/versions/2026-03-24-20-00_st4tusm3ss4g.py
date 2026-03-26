"""Add status_message column to llm_runtimes.

Revision ID: st4tusm3ss4g
Revises: c10ud_r3m0t3
Create Date: 2026-03-24 20:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "st4tusm3ss4g"
down_revision = "c10ud_r3m0t3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_runtimes",
        sa.Column("status_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_runtimes", "status_message")
