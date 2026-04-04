"""Add is_builtin column to roles table and groups support.

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-02-28 12-00-00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "g7h8i9j0k1l2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Sprint 1: is_builtin flag on roles ---
    op.add_column(
        "roles",
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Mark existing seeded roles as built-in
    op.execute(
        "UPDATE roles SET is_builtin = true WHERE name IN "
        "('admin', 'operator', 'viewer', 'rag_manager', 'rag_editor', 'rag_viewer')"
    )

    # --- Sprint 2: Groups ---
    op.create_table(
        "groups",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "group_roles",
        sa.Column(
            "group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "user_groups",
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # --- Sprint 3: OAuth provider registry ---
    op.create_table(
        "oauth_account",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("oauth_name", sa.String(100), nullable=False, index=True),
        sa.Column("access_token", sa.String(1024), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=True),
        sa.Column("refresh_token", sa.String(1024), nullable=True),
        sa.Column("account_id", sa.String(320), nullable=False, index=True),
        sa.Column("account_email", sa.String(320), nullable=True),
    )

    op.create_table(
        "auth_providers",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("provider_type", sa.String(32), nullable=False),
        sa.Column("client_id", sa.String(512), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("discovery_url", sa.String(1024), nullable=True),
        sa.Column("authorize_url", sa.String(1024), nullable=True),
        sa.Column("token_url", sa.String(1024), nullable=True),
        sa.Column("userinfo_url", sa.String(1024), nullable=True),
        sa.Column("scopes", sa.String(512), nullable=False, server_default="openid email profile"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("auto_register", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("default_role_ids", sa.dialects.postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("group_mapping", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("auth_providers")
    op.drop_table("oauth_account")
    op.drop_table("user_groups")
    op.drop_table("group_roles")
    op.drop_table("groups")
    op.drop_column("roles", "is_builtin")
