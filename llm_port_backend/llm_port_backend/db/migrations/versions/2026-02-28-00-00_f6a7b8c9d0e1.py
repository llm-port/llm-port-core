"""Add remote_endpoint provider target and provider endpoint columns.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-02-28 00-00-00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend the provider_target enum with 'remote_endpoint'
    op.execute("ALTER TYPE provider_target ADD VALUE IF NOT EXISTS 'remote_endpoint'")

    # Add endpoint_url and api_key_encrypted columns to llm_providers
    op.add_column(
        "llm_providers",
        sa.Column("endpoint_url", sa.String(1024), nullable=True),
    )
    op.add_column(
        "llm_providers",
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_providers", "api_key_encrypted")
    op.drop_column("llm_providers", "endpoint_url")
    # Note: PostgreSQL does not support removing values from an enum type.
    # The 'remote_endpoint' value will remain in the enum but be unused.
