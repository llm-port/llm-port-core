"""Node control-plane models for cluster-managed runtimes."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from llm_port_backend.db.base import Base


class NodeHealthStatus(enum.StrEnum):
    """Health state exposed by a node."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DRAINING = "draining"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"
    ERROR = "error"


class NodeCommandStatus(enum.StrEnum):
    """Execution lifecycle for control-plane commands."""

    QUEUED = "queued"
    DISPATCHED = "dispatched"
    ACKED = "acked"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"


class NodeCommandType(enum.StrEnum):
    """Well-known node command actions."""

    DEPLOY_WORKLOAD = "deploy_workload"
    START_WORKLOAD = "start_workload"
    STOP_WORKLOAD = "stop_workload"
    RESTART_WORKLOAD = "restart_workload"
    REMOVE_WORKLOAD = "remove_workload"
    UPDATE_WORKLOAD = "update_workload"
    REFRESH_INVENTORY = "refresh_inventory"
    SET_MAINTENANCE_MODE = "set_maintenance_mode"
    DRAIN_NODE = "drain_node"
    RESUME_NODE = "resume_node"
    COLLECT_DIAGNOSTICS = "collect_diagnostics"
    SYNC_MODEL = "sync_model"
    FETCH_CONTAINER_LOGS = "fetch_container_logs"
    HOST_OP = "host_op"
    SYNC_NODE_PROFILE = "sync_node_profile"
    CHECK_SYSTEM_UPDATES = "check_system_updates"
    APPLY_SYSTEM_UPDATES = "apply_system_updates"

    # --- Ray environment lifecycle (Phase 2) ---
    ENSURE_RAY_RUNTIME = "ensure_ray_runtime"
    START_RAY_HEAD = "start_ray_head"
    JOIN_RAY_CLUSTER = "join_ray_cluster"
    LEAVE_RAY_CLUSTER = "leave_ray_cluster"
    STOP_RAY = "stop_ray"
    GET_RAY_STATUS = "get_ray_status"
    GET_RAY_SERVE_STATUS = "get_ray_serve_status"

    # --- Ray Serve application lifecycle (Phase 3) ---
    RUN_SERVE_APP = "run_serve_app"
    DELETE_SERVE_APP = "delete_serve_app"

    # --- Fabric planning / active validation (Phase 4A) ---
    VALIDATE_FABRIC_LISTEN = "validate_fabric_listen"
    VALIDATE_FABRIC_CONNECT = "validate_fabric_connect"

    # --- Runtime bundle readiness (Phase 4B) ---
    ENSURE_RUNTIME_IMAGE = "ensure_runtime_image"


class InfraNodeProfile(Base):
    """Reusable profile with platform-specific sub-configs for nodes."""

    __tablename__ = "infra_node_profile"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    runtime_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    gpu_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    storage_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    network_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    logging_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    security_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    update_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class InfraNode(Base):
    """Authoritative node record in backend control plane."""

    __tablename__ = "infra_node"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    host: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default=NodeHealthStatus.OFFLINE.value)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    labels_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    draining: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scheduler_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node_profile.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class InfraNodeEnrollmentToken(Base):
    """One-time onboarding token generated by admins."""

    __tablename__ = "infra_node_enrollment_token"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by_node_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class JoinRequestStatus(enum.StrEnum):
    """Lifecycle of a machine asking to be let into the fleet."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    #: The agent collected its credential; the request is spent.
    CLAIMED = "claimed"


class InfraNodeJoinRequest(Base):
    """A machine asking to join, waiting for a human to say yes.

    This exists because the enrollment-token direction only works when the
    operator's browser and a shell on the new machine share a clipboard.  When
    they do not -- someone standing at the box, or connected from a different
    workstation -- a 32-character token has to be retyped by hand, and that is
    the single worst moment in onboarding.

    So the secret travels the other way.  The machine asks, the backend shows
    the request, and an administrator approves it in the browser.  Nothing
    long is ever typed.

    ``code`` is deliberately short and is **not** a secret: it exists so the
    operator approves the machine they are looking at rather than one that
    happened to ask at the same moment.  Safety comes from the approval being
    an authenticated action, from the reported identity being shown before the
    click, and from ``poll_secret_hash`` -- which only the requesting agent can
    satisfy, so guessing a code still collects nothing.
    """

    __tablename__ = "infra_node_join_request"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Short, human-comparable, unambiguous alphabet.  Unique among pending.
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: Only the agent that made the request holds the matching secret.
    poll_secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    #: What the machine says it is.  Shown to the operator *before* approval,
    #: because "approve this" is only meaningful if you can see what "this" is.
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Where the request actually came from, which may differ from what the
    #: machine claims.  A mismatch is worth showing rather than hiding.
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=JoinRequestStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    #: Set on approval; the agent's poll turns this into a credential once.
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("infra_node.id", ondelete="CASCADE"), nullable=True
    )
    #: Why it was rejected, or why it could not be approved.
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class InfraNodeCredential(Base):
    """Per-node rotating API credential."""

    __tablename__ = "infra_node_credential"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InfraNodeSession(Base):
    """Logical stream session for an agent connection."""

    __tablename__ = "infra_node_session"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node_credential.id", ondelete="CASCADE"),
        nullable=False,
    )
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rx_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class InfraNodeInventorySnapshot(Base):
    """Periodic inventory/utilization payload reported by node."""

    __tablename__ = "infra_node_inventory_snapshot"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    inventory_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    utilization_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class InfraNodeCommand(Base):
    """Command queued by backend for node execution."""

    __tablename__ = "infra_node_command"
    __table_args__ = (UniqueConstraint("node_id", "idempotency_key", name="uq_infra_node_command_idempotency"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    command_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default=NodeCommandStatus.QUEUED.value)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    timeout_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("clock_timestamp()"),
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class InfraNodeCommandEvent(Base):
    """Immutable timeline entries for node command lifecycle."""

    __tablename__ = "infra_node_command_event"
    __table_args__ = (UniqueConstraint("command_id", "seq", name="uq_infra_node_command_event_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    command_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node_command.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class InfraNodeEvent(Base):
    """Generic operational events emitted by node agent."""

    __tablename__ = "infra_node_event"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="info")
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class InfraNodeMaintenanceWindow(Base):
    """Maintenance schedule and audit trail for node availability changes."""

    __tablename__ = "infra_node_maintenance_window"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class InfraNodeWorkloadAssignment(Base):
    """Current runtime assignment to a managed node."""

    __tablename__ = "infra_node_workload_assignment"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runtime_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_runtimes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    desired_state: Mapped[str] = mapped_column(String(64), nullable=False, default="running")
    actual_state: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
