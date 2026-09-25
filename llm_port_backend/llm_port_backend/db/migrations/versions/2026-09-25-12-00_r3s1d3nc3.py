"""Providers can say where their prompts go: an administrator's residency override.

Residency is detected from where the endpoint is (services/llm/residency.py),
and an address cannot tell a LAN proxy that forwards to a cloud API from a
self-hosted model, or a cloud VPC on private addresses from the office network.
NULL means detected.

Revision ID: r3s1d3nc3
Revises: us4g3gr0up
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r3s1d3nc3"
down_revision = "us4g3gr0up"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_providers", sa.Column("residency_override", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_providers", "residency_override")
