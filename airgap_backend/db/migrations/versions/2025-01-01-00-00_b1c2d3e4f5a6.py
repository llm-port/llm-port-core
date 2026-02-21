"""Create user table.

Revision ID: b1c2d3e4f5a6
Revises: 2b7380507a71
Create Date: 2025-01-01 00-00-00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "2b7380507a71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the user table required by fastapi-users."""
    op.create_table(
        "user",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)


def downgrade() -> None:
    """Drop the user table."""
    op.drop_index("ix_user_email", table_name="user")
    op.drop_table("user")
