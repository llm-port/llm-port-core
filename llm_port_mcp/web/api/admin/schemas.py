"""Pydantic schemas for MCP admin API requests and responses."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Request schemas ───────────────────────────────────────────────────


class RegisterServerRequest(BaseModel):
    """Register a new MCP server."""

    name: str = Field(min_length=1, max_length=256)
    transport: str = Field(pattern=r"^(stdio|sse)$")
    url: str | None = None
    command: list[str] | None = None
    args: list[str] = Field(default_factory=list)
    working_dir: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    tool_prefix: str = Field(min_length=1, max_length=128)
    pii_mode: str = Field(default="redact", pattern=r"^(allow|redact|block)$")
    timeout_sec: int = Field(default=60, ge=5, le=600)
    heartbeat_interval_sec: int = Field(default=30, ge=5, le=300)
    auto_discover: bool = True
    enabled: bool = True
    tenant_id: str = Field(default="default", max_length=128)


class UpdateServerRequest(BaseModel):
    """Partial update to a server config."""

    name: str | None = None
    url: str | None = None
    command: list[str] | None = None
    args: list[str] | None = None
    working_dir: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    tool_prefix: str | None = None
    pii_mode: str | None = Field(default=None, pattern=r"^(allow|redact|block)$")
    timeout_sec: int | None = Field(default=None, ge=5, le=600)
    heartbeat_interval_sec: int | None = Field(default=None, ge=5, le=300)
    enabled: bool | None = None


class UpdateToolRequest(BaseModel):
    """Partial update to a tool."""

    enabled: bool | None = None
    display_name: str | None = None


# ── Response schemas ──────────────────────────────────────────────────


class ToolResponse(BaseModel):
    """A single tool as returned by the API."""

    id: str
    server_id: str
    qualified_name: str
    upstream_name: str
    display_name: str | None = None
    description: str
    enabled: bool
    version: str
    schema_hash: str
    input_schema: dict[str, Any] | None = None
    openai_schema: dict[str, Any] | None = None
    last_seen_at: datetime | None = None


class ServerResponse(BaseModel):
    """An MCP server as returned by the API."""

    id: str
    name: str
    transport: str
    status: str
    url: str | None = None
    command: list[str] | None = None
    args: list[str] | None = None
    working_dir: str | None = None
    tool_prefix: str
    pii_mode: str
    enabled: bool
    timeout_sec: int
    heartbeat_interval_sec: int
    tenant_id: str
    discovered_tools: int = 0
    created_at: datetime
    updated_at: datetime
    last_discovery_at: datetime | None = None
    tools: list[ToolResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ServerListResponse(BaseModel):
    """List of servers."""

    servers: list[ServerResponse]
    total: int
