"""Container management models: registry, stacks, audit, root sessions."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from llm_port_backend.db.base import Base


class ContainerClass(enum.StrEnum):
    """Classification of containers for policy enforcement."""

    SYSTEM_CORE = "SYSTEM_CORE"
    SYSTEM_AUX = "SYSTEM_AUX"
    MCP = "MCP"
    TENANT_APP = "TENANT_APP"
    UNTRUSTED = "UNTRUSTED"


class ContainerPolicy(enum.StrEnum):
    """Policy level applied to a container."""

    LOCKED = "locked"
    RESTRICTED = "restricted"
    FREE = "free"


class AuditResult(enum.StrEnum):
    """Outcome of an audited action."""

    ALLOW = "allow"
    DENY = "deny"


class ContainerRegistry(Base):
    """Server-side registry — single source of truth for container classification."""

    __tablename__ = "container_registry"

    container_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    container_class: Mapped[ContainerClass] = mapped_column(
        SAEnum(ContainerClass, name="container_class", create_type=False),
        nullable=False,
        default=ContainerClass.UNTRUSTED,
    )
    owner_scope: Mapped[str] = mapped_column(String(256), nullable=False, default="platform")
    policy: Mapped[ContainerPolicy] = mapped_column(
        SAEnum(
            ContainerPolicy,
            name="container_policy",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ContainerPolicy.FREE,
    )
    engine_id: Mapped[str] = mapped_column(String(128), nullable=False, default="local")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class StackRevision(Base):
    """Versioned record of a compose stack deployment."""

    __tablename__ = "stack_revisions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    stack_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    rev: Mapped[int] = mapped_column(nullable=False)
    compose_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    env_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_digests: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON blob
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AuditEvent(Base):
    """Immutable audit log entry for every mutating action."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(256), nullable=False)
    result: Mapped[AuditResult] = mapped_column(
        SAEnum(
            AuditResult,
            name="audit_result",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class RootSession(Base):
    """Break-glass root mode session record."""

    __tablename__ = "root_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(256), nullable=False, default="all")
    duration_seconds: Mapped[int] = mapped_column(nullable=False, default=600)


def utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(tz=UTC)
