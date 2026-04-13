"""Add session_pii_override table for per-session PII policy overrides.

Introduces:
  - ``session_pii_override`` table (1:1 with ``chat_session``)

Revision ID: 3g4h5i6j7k8l
Revises: 2f3a4b5c6d7e
Create Date: 2026-04-10 10:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "3g4h5i6j7k8l"
down_revision = "2f3a4b5c6d7e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Raw SQL pattern (asyncpg-safe, no sa.Enum objects).
    op.execute(
        "CREATE TABLE session_pii_override ("
        "  session_id UUID PRIMARY KEY REFERENCES chat_session(id) ON DELETE CASCADE,"
        "  pii_enabled BOOLEAN,"
        "  egress_enabled_for_cloud BOOLEAN,"
        "  egress_enabled_for_local BOOLEAN,"
        "  egress_mode VARCHAR(32),"
        "  egress_fail_action VARCHAR(32),"
        "  telemetry_enabled BOOLEAN,"
        "  telemetry_mode VARCHAR(32),"
        "  presidio_threshold FLOAT CHECK (presidio_threshold >= 0 AND presidio_threshold <= 1),"
        "  presidio_entities_add JSONB,"
        "  updated_by VARCHAR(128),"
        "  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )


def downgrade() -> None:
    op.drop_table("session_pii_override")
