"""Add MCP value to container_class enum.

Revision ID: x6y7z8a9b0c1
Revises: w5x6y7z8a9b0
Create Date: 2026-03-15 10:00:00.000000

Changes:
- Add 'MCP' to the container_class PostgreSQL enum type.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "x6y7z8a9b0c1"
down_revision = "w5x6y7z8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE container_class ADD VALUE IF NOT EXISTS 'MCP'")


def downgrade() -> None:
    # PostgreSQL does not support removing individual enum values.
    # A full enum rebuild would be needed; skip for safety.
    pass
