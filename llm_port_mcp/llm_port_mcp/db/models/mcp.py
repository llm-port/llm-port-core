"""SQLAlchemy ORM models for MCP server and tool registry."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for MCP models."""


# ── Enums ─────────────────────────────────────────────────────────────


class MCPTransportType(str, enum.Enum):
    """Supported MCP transport types."""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerStatus(str, enum.Enum):
    """Server lifecycle states."""

    REGISTERING = "registering"
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    DISABLED = "disabled"


class PIIMode(str, enum.Enum):
    """Privacy proxy enforcement modes."""

    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"


# ── Models ────────────────────────────────────────────────────────────


class MCPServerModel(Base):
    """A registered MCP server that provides tools."""

    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_mcp_server_tenant_name"),
        UniqueConstraint(
            "tenant_id", "tool_prefix", name="uq_mcp_server_tenant_prefix"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="default",
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    command_json: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    args_json: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, default=list
    )
    working_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    headers_json_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    env_json_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_prefix: Mapped[str] = mapped_column(String(128), nullable=False)
    pii_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="redact",
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="registering",
    )
    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    heartbeat_interval_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
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
    last_discovery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ── Provider settings (dynamic, proxied from remote MCP server) ──
    settings_schema_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    provider_settings_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    tools: Mapped[list[MCPToolModel]] = relationship(
        back_populates="server",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class MCPToolModel(Base):
    """A tool discovered from an MCP server."""

    __tablename__ = "mcp_tools"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "qualified_name",
            name="uq_mcp_tool_tenant_qualified",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    server_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="default",
        index=True,
    )
    upstream_name: Mapped[str] = mapped_column(String(256), nullable=False)
    qualified_name: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_schema_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    openai_schema_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    annotations_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    server: Mapped[MCPServerModel] = relationship(back_populates="tools")
