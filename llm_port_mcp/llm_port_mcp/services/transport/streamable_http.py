"""Streamable HTTP transport adapter for remote MCP servers.

Uses the MCP SDK's ``streamable_http_client`` (MCP spec 2025-03-26) to
connect to servers that expose a ``StreamableHTTPServerTransport`` endpoint.
"""

from __future__ import annotations

import contextlib
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from llm_port_mcp.services.transport.base import (
    GenericMCPServer,
    MCPServerConfig,
    MCPToolDescriptor,
)

logger = logging.getLogger(__name__)


class StreamableHTTPMCPServer(GenericMCPServer):
    """MCP server connected via Streamable HTTP (remote)."""

    def __init__(self, config: MCPServerConfig) -> None:
        super().__init__(config)
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._http_client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """Open a Streamable HTTP connection to the remote MCP server."""
        if not self.config.url:
            msg = "Streamable HTTP transport requires 'url'"
            raise ValueError(msg)

        stack = AsyncExitStack()
        try:
            # Build an httpx client with custom headers / timeout
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=self.config.headers or {},
                    timeout=httpx.Timeout(self.config.timeout_sec, read=300),
                ),
            )

            read_stream, write_stream, _ = (
                await stack.enter_async_context(
                    streamable_http_client(
                        url=self.config.url,
                        http_client=http_client,
                    ),
                )
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream),
            )
            await session.initialize()
        except Exception:
            # Clean up the exit stack before propagating so the MCP SDK's
            # internal task-group / cancel-scope is torn down in the same
            # task that created it — avoids the anyio RuntimeError.
            with contextlib.suppress(Exception):
                await stack.aclose()
            raise

        # Only persist state after a fully successful connect.
        self._exit_stack = stack
        self._http_client = http_client
        self._session = session
        logger.info(
            "Streamable HTTP MCP server connected: %s (url=%s)",
            self.config.name,
            self.config.url,
        )

    async def disconnect(self) -> None:
        """Close the Streamable HTTP connection."""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            self._http_client = None
        logger.info(
            "Streamable HTTP MCP server disconnected: %s",
            self.config.name,
        )

    async def healthcheck(self) -> str:
        """Check if the session is alive."""
        if self._session is None:
            return "disconnected"
        try:
            await self._session.send_ping()
            return "active"
        except Exception:
            return "error"

    async def list_tools(self) -> list[MCPToolDescriptor]:
        """Discover tools from the remote MCP server."""
        if self._session is None:
            msg = "Not connected"
            raise RuntimeError(msg)

        result = await self._session.list_tools()
        tools = []
        for tool in result.tools:
            tools.append(
                MCPToolDescriptor(
                    upstream_name=tool.name,
                    qualified_name=f"mcp.{self.config.tool_prefix}.{tool.name}",
                    description=tool.description or "",
                    input_schema=(
                        tool.inputSchema
                        if isinstance(tool.inputSchema, dict)
                        else {}
                    ),
                    title=getattr(tool, "title", None),
                    annotations=(
                        tool.annotations.model_dump()
                        if getattr(tool, "annotations", None)
                        else {}
                    ),
                ),
            )
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a tool on the remote MCP server."""
        if self._session is None:
            msg = "Not connected"
            raise RuntimeError(msg)

        result = await self._session.call_tool(tool_name, arguments)

        content_parts = []
        for item in result.content:
            part: dict[str, Any] = {"type": item.type}
            if hasattr(item, "text"):
                part["text"] = item.text
            elif hasattr(item, "data"):
                part["data"] = item.data
                if hasattr(item, "mimeType"):
                    part["mimeType"] = item.mimeType
            content_parts.append(part)

        return {
            "content": content_parts,
            "isError": result.isError if hasattr(result, "isError") else False,
        }
