"""Add allow_mode_override column to tenant_llm_policy.

Allows admins to opt-in to letting session overrides switch PII egress mode
(e.g. redact ↔ tokenize_reversible).  Default is False (no mode switching).

Revision ID: 4h5i6j7k8l9m
Revises: 3g4h5i6j7k8l
Create Date: 2026-04-10 11:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "4h5i6j7k8l9m"
down_revision = "3g4h5i6j7k8l"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_llm_policy "
        "ADD COLUMN IF NOT EXISTS allow_mode_override BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_llm_policy "
        "DROP COLUMN IF EXISTS allow_mode_override"
    )
