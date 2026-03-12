"""Add LiteLLM columns to llm_providers table.

Revision ID: w5x6y7z8a9b0
Revises: v4w5x6y7z8a9
Create Date: 2026-03-12 10:00:00.000000

Changes:
- Add litellm_provider (varchar(64), nullable) to llm_providers.
- Add litellm_model (varchar(256), nullable) to llm_providers.
- Add extra_params (json, nullable) to llm_providers.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "w5x6y7z8a9b0"
down_revision = "v4w5x6y7z8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_providers",
        sa.Column("litellm_provider", sa.String(64), nullable=True),
    )
    op.add_column(
        "llm_providers",
        sa.Column("litellm_model", sa.String(256), nullable=True),
    )
    op.add_column(
        "llm_providers",
        sa.Column("extra_params", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_providers", "extra_params")
    op.drop_column("llm_providers", "litellm_model")
    op.drop_column("llm_providers", "litellm_provider")
