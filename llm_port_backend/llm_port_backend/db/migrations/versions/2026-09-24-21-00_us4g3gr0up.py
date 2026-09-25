"""Users get a usage group: the group their LLM usage is attributed to.

Groups are RBAC containers and a user can be in several. Usage reports -- and
the budgets the enterprise edition builds on them -- need exactly one answer
to "whose usage is this", so the user carries it. NULL means unattributed;
deleting the group clears it rather than blocking the delete.

Revision ID: us4g3gr0up
Revises: h1br1dsrch
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "us4g3gr0up"
down_revision = "h1br1dsrch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "usage_group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="SET NULL", name="fk_user_usage_group_id_groups"),
            nullable=True,
        ),
    )
    op.create_index("ix_user_usage_group_id", "user", ["usage_group_id"])


def downgrade() -> None:
    op.drop_index("ix_user_usage_group_id", table_name="user")
    op.drop_column("user", "usage_group_id")
