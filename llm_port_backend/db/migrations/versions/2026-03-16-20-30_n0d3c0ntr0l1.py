"""Add node control-plane tables and runtime node-assignment fields.

Revision ID: n0d3c0ntr0l1
Revises: x6y7z8a9b0c1
Create Date: 2026-03-16 20:30:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "n0d3c0ntr0l1"
down_revision = "x6y7z8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "infra_node",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("host", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="offline"),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("labels_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "capabilities_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("maintenance_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("draining", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("scheduler_eligible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_infra_node_agent_id", "infra_node", ["agent_id"], unique=True)

    op.create_table(
        "infra_node_enrollment_token",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("issued_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["issued_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["used_by_node_id"], ["infra_node.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_infra_node_enrollment_token_hash", "infra_node_enrollment_token", ["token_hash"], unique=True)

    op.create_table(
        "infra_node_credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["infra_node.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_infra_node_credential_node_id", "infra_node_credential", ["node_id"])

    op.create_table(
        "infra_node_session",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rx_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["node_id"], ["infra_node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credential_id"], ["infra_node_credential.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_infra_node_session_node_id", "infra_node_session", ["node_id"])

    op.create_table(
        "infra_node_inventory_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "inventory_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "utilization_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["node_id"], ["infra_node.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_infra_node_inventory_snapshot_node_id", "infra_node_inventory_snapshot", ["node_id"])

    op.create_table(
        "infra_node_command",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="queued"),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timeout_sec", sa.Integer(), nullable=True),
        sa.Column("issued_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["infra_node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issued_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "idempotency_key", name="uq_infra_node_command_idempotency"),
    )
    op.create_index("ix_infra_node_command_node_id", "infra_node_command", ["node_id"])

    op.create_table(
        "infra_node_command_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["command_id"], ["infra_node_command.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", "seq", name="uq_infra_node_command_event_seq"),
    )
    op.create_index("ix_infra_node_command_event_command_id", "infra_node_command_event", ["command_id"])

    op.create_table(
        "infra_node_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False, server_default="info"),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["node_id"], ["infra_node.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_infra_node_event_node_id", "infra_node_event", ["node_id"])

    op.create_table(
        "infra_node_maintenance_window",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="active"),
        sa.ForeignKeyConstraint(["node_id"], ["infra_node.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_infra_node_maintenance_window_node_id", "infra_node_maintenance_window", ["node_id"])

    op.create_table(
        "infra_node_workload_assignment",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("desired_state", sa.String(length=64), nullable=False, server_default="running"),
        sa.Column("actual_state", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["runtime_id"], ["llm_runtimes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["infra_node.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("runtime_id"),
    )
    op.create_index("ix_infra_node_workload_assignment_runtime_id", "infra_node_workload_assignment", ["runtime_id"])
    op.create_index("ix_infra_node_workload_assignment_node_id", "infra_node_workload_assignment", ["node_id"])

    op.add_column("llm_runtimes", sa.Column("execution_target", sa.String(length=32), nullable=False, server_default="local"))
    op.add_column("llm_runtimes", sa.Column("assigned_node_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("llm_runtimes", sa.Column("desired_state", sa.String(length=64), nullable=False, server_default="running"))
    op.add_column("llm_runtimes", sa.Column("placement_explain_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("llm_runtimes", sa.Column("last_command_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_llm_runtimes_assigned_node_id", "llm_runtimes", ["assigned_node_id"])
    op.create_foreign_key(
        "fk_llm_runtimes_assigned_node_id",
        "llm_runtimes",
        "infra_node",
        ["assigned_node_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_llm_runtimes_last_command_id",
        "llm_runtimes",
        "infra_node_command",
        ["last_command_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_llm_runtimes_last_command_id", "llm_runtimes", type_="foreignkey")
    op.drop_constraint("fk_llm_runtimes_assigned_node_id", "llm_runtimes", type_="foreignkey")
    op.drop_index("ix_llm_runtimes_assigned_node_id", table_name="llm_runtimes")
    op.drop_column("llm_runtimes", "last_command_id")
    op.drop_column("llm_runtimes", "placement_explain_json")
    op.drop_column("llm_runtimes", "desired_state")
    op.drop_column("llm_runtimes", "assigned_node_id")
    op.drop_column("llm_runtimes", "execution_target")

    op.drop_index("ix_infra_node_workload_assignment_node_id", table_name="infra_node_workload_assignment")
    op.drop_index("ix_infra_node_workload_assignment_runtime_id", table_name="infra_node_workload_assignment")
    op.drop_table("infra_node_workload_assignment")
    op.drop_index("ix_infra_node_maintenance_window_node_id", table_name="infra_node_maintenance_window")
    op.drop_table("infra_node_maintenance_window")
    op.drop_index("ix_infra_node_event_node_id", table_name="infra_node_event")
    op.drop_table("infra_node_event")
    op.drop_index("ix_infra_node_command_event_command_id", table_name="infra_node_command_event")
    op.drop_table("infra_node_command_event")
    op.drop_index("ix_infra_node_command_node_id", table_name="infra_node_command")
    op.drop_table("infra_node_command")
    op.drop_index("ix_infra_node_inventory_snapshot_node_id", table_name="infra_node_inventory_snapshot")
    op.drop_table("infra_node_inventory_snapshot")
    op.drop_index("ix_infra_node_session_node_id", table_name="infra_node_session")
    op.drop_table("infra_node_session")
    op.drop_index("ix_infra_node_credential_node_id", table_name="infra_node_credential")
    op.drop_table("infra_node_credential")
    op.drop_index("ix_infra_node_enrollment_token_hash", table_name="infra_node_enrollment_token")
    op.drop_table("infra_node_enrollment_token")
    op.drop_index("ix_infra_node_agent_id", table_name="infra_node")
    op.drop_table("infra_node")
