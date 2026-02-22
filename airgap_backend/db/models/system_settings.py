"""System settings and apply orchestration models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from airgap_backend.db.base import Base


class SystemApplyScope(enum.StrEnum):
    """Impact scope of a setting change."""

    LIVE_RELOAD = "live_reload"
    SERVICE_RESTART = "service_restart"
    STACK_RECREATE = "stack_recreate"


class SystemApplyStatus(enum.StrEnum):
    """Execution status for apply jobs."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLBACK_RUNNING = "rollback_running"
    ROLLBACK_FAILED = "rollback_failed"


class SystemApplyEventResult(enum.StrEnum):
    """Per-event result for apply job logs."""

    INFO = "info"
    SUCCESS = "success"
    FAILED = "failed"


class InfraAgentStatus(enum.StrEnum):
    """Agent heartbeat status."""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class SystemSettingValue(Base):
    """Non-secret settings value store."""

    __tablename__ = "system_setting_value"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SystemSettingSecret(Base):
    """Encrypted secret settings store."""

    __tablename__ = "system_setting_secret"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str | None] = mapped_column(String(256), nullable=True)
    kek_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SystemApplyJob(Base):
    """Apply execution metadata."""

    __tablename__ = "system_apply_job"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=SystemApplyStatus.PENDING.value,
    )
    target_host: Mapped[str] = mapped_column(String(256), nullable=False, default="local")
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    change_set_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    previous_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class SystemApplyJobEvent(Base):
    """Ordered events for apply job execution."""

    __tablename__ = "system_apply_job_event"
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_system_apply_job_event_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("system_apply_job.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    service: Mapped[str] = mapped_column(String(256), nullable=False)
    action: Mapped[str] = mapped_column(String(256), nullable=False)
    result: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=SystemApplyEventResult.INFO.value,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class InfraAgent(Base):
    """Registered infrastructure agent."""

    __tablename__ = "infra_agent"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    host: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=InfraAgentStatus.OFFLINE.value,
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
