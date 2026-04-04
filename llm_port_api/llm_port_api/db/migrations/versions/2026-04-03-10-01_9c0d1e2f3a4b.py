"""Create price_catalog table.

Revision ID: 9c0d1e2f3a4b
Revises: 8b9c0d1e2f3a
Create Date: 2026-04-03 10:01:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "9c0d1e2f3a4b"
down_revision = "8b9c0d1e2f3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("input_price_per_1k", sa.Numeric(12, 8), nullable=False),
        sa.Column("output_price_per_1k", sa.Numeric(12, 8), nullable=False),
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # Partial unique index: one active price per provider/model pair
    op.execute(
        "CREATE UNIQUE INDEX ix_price_catalog_active "
        "ON price_catalog (provider, model) WHERE active = true"
    )
    # Lookup index
    op.create_index(
        "ix_price_catalog_provider_model_active",
        "price_catalog",
        ["provider", "model", "active"],
    )
    # FK from request log to price catalog
    op.create_foreign_key(
        "fk_request_log_price_catalog",
        "llm_gateway_request_log",
        "price_catalog",
        ["price_catalog_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_request_log_price_catalog",
        "llm_gateway_request_log",
        type_="foreignkey",
    )
    op.drop_index("ix_price_catalog_provider_model_active", table_name="price_catalog")
    op.execute("DROP INDEX IF EXISTS ix_price_catalog_active")
    op.drop_table("price_catalog")
