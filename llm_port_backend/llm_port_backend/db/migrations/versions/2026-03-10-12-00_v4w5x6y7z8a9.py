"""Add pii_scan_events table to backend DB.

Consolidates PII event storage from the separate PII service database
into the backend database so that PII dashboard stats and events are
served directly without proxying to the PII micro-service.

Revision ID: v4w5x6y7z8a9
Revises: u3v4w5x6y7z8
Create Date: 2026-03-10 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON, UUID

# revision identifiers, used by Alembic.
revision = "v4w5x6y7z8a9"
down_revision = "u3v4w5x6y7z8"
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
