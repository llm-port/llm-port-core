"""Add container management tables.

Revision ID: a1b2c3d4e5f6
Revises: 2b7380507a71
Create Date: 2026-02-20 00-00-00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

container_class_enum = sa.Enum(
    "SYSTEM_CORE", "SYSTEM_AUX", "TENANT_APP", "UNTRUSTED",
    name="container_class",
)
container_policy_enum = sa.Enum(
    "locked", "restricted", "free",
    name="container_policy",
)
audit_result_enum = sa.Enum(
    "allow", "deny",
    name="audit_result",
)


def upgrade() -> None:
    """Run the upgrade migrations."""
    # Let op.create_table's before_create DDL events handle enum type creation.
    # Do NOT create types manually — the PostgreSQL dialect's _on_table_create
    # fires with checkfirst=False and ignores sa.Enum's create_type flag,
    # so any prior CREATE TYPE would cause DuplicateObjectError.

    op.create_table(
        "container_registry",
        sa.Column("container_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "container_class",
            sa.Enum(
                "SYSTEM_CORE", "SYSTEM_AUX", "TENANT_APP", "UNTRUSTED",
                name="container_class",
            ),
            nullable=False,
            server_default="UNTRUSTED",
        ),
        sa.Column("owner_scope", sa.String(length=256), nullable=False, server_default="platform"),
        sa.Column(
            "policy",
            sa.Enum("locked", "restricted", "free", name="container_policy"),
            nullable=False,
            server_default="free",
        ),
        sa.Column("engine_id", sa.String(length=128), nullable=False, server_default="local"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("container_id"),
    )

    op.create_table(
        "stack_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stack_id", sa.String(length=256), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("compose_yaml", sa.Text(), nullable=False),
        sa.Column("env_blob", sa.Text(), nullable=True),
        sa.Column("image_digests", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stack_revisions_stack_id", "stack_revisions", ["stack_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=256), nullable=False),
        sa.Column(
            "result",
            sa.Enum("allow", "deny", name="audit_result"),
            nullable=False,
        ),
        sa.Column("severity", sa.String(length=32), nullable=False, server_default="normal"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_time", "audit_events", ["time"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])

    op.create_table(
        "root_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "start_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=256), nullable=False, server_default="all"),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="600"),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_root_sessions_actor_id", "root_sessions", ["actor_id"])


def downgrade() -> None:
    """Run the downgrade migrations."""
    op.drop_table("root_sessions")
    op.drop_table("audit_events")
    op.drop_table("stack_revisions")
    op.drop_table("container_registry")

    audit_result_enum.drop(op.get_bind(), checkfirst=True)
    container_policy_enum.drop(op.get_bind(), checkfirst=True)
    container_class_enum.drop(op.get_bind(), checkfirst=True)
