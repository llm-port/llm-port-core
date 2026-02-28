"""Add pii_scan_events table.

Revision ID: a1b2c3d4e5f6
Revises: 2b7380507a71
Create Date: 2026-01-20 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON, UUID

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "2b7380507a71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the pii_scan_events table."""
    op.create_table(
        "pii_scan_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column("operation", sa.String(32), nullable=False, index=True),
        sa.Column("mode", sa.String(32), nullable=True),
        sa.Column("language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("score_threshold", sa.Float, nullable=False, server_default="0.6"),
        sa.Column("pii_detected", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("entities_found", sa.Integer, nullable=False, server_default="0"),
        sa.Column("entity_type_counts", JSON, nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="api"),
        sa.Column("request_id", sa.String(128), nullable=True, index=True),
    )


def downgrade() -> None:
    """Drop the pii_scan_events table."""
    op.drop_table("pii_scan_events")
