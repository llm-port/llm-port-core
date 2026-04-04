"""Abstract base class for MCP server transport adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MCPToolDescriptor:
    """Normalized tool metadata discovered from an MCP server."""

    upstream_name: str
    qualified_name: str
    description: str
    input_schema: dict[str, Any]
    title: str | None = None
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration needed to connect to an MCP server."""

    id: str
    name: str
    transport: str
    url: str | None = None
    command: list[str] | None = None
    args: list[str] | None = None
    working_dir: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    timeout_sec: int = 60
    tool_prefix: str = ""


class GenericMCPServer(ABC):
    """Abstract base for transport-specific MCP server connections."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config

    @abstractmethod
    async def connect(self) -> None:
        """Establish session/process/transport connectivity."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close all active resources cleanly."""

    @abstractmethod
    async def healthcheck(self) -> str:
        """Return current health state as a status string."""

    @abstractmethod
    async def list_tools(self) -> list[MCPToolDescriptor]:
        """Discover tools from the upstream MCP server."""

    @abstractmethod
    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke the upstream MCP tool and return normalized result."""

    @property
    def server_id(self) -> str:
        """Return the server config ID."""
        return self.config.id
