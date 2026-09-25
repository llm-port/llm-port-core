"""Request log rows carry a usage group and a project.

Usage is attributed to the group the backend stores on the user
(``user.usage_group_id``) and to the chat project the session belongs to, so
it can be reported per team and per project without joining across databases.

Revision ID: 7l8m9n0p1q2r
Revises: 6k7l8m9n0p1q
Create Date: 2026-09-24 21:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "7l8m9n0p1q2r"
down_revision = "6k7l8m9n0p1q"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_gateway_request_log ADD COLUMN IF NOT EXISTS group_id VARCHAR(128)")
    op.execute("ALTER TABLE llm_gateway_request_log ADD COLUMN IF NOT EXISTS project_id VARCHAR(128)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_gateway_request_log_group_id "
        "ON llm_gateway_request_log (group_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_llm_gateway_request_log_group_id")
    op.execute("ALTER TABLE llm_gateway_request_log DROP COLUMN IF EXISTS project_id")
    op.execute("ALTER TABLE llm_gateway_request_log DROP COLUMN IF EXISTS group_id")
