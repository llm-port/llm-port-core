"""SSE transport adapter for remote MCP servers."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

from llm_port_mcp.services.transport.base import (
    GenericMCPServer,
    MCPServerConfig,
    MCPToolDescriptor,
)

logger = logging.getLogger(__name__)


class SSEMCPServer(GenericMCPServer):
    """MCP server connected via Server-Sent Events (remote)."""

    def __init__(self, config: MCPServerConfig) -> None:
        super().__init__(config)
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def connect(self) -> None:
        """Open an SSE connection to the remote MCP server."""
        if not self.config.url:
            msg = "SSE transport requires 'url'"
            raise ValueError(msg)

        self._exit_stack = AsyncExitStack()
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            sse_client(
                url=self.config.url,
                headers=self.config.headers or {},
                timeout=self.config.timeout_sec,
            ),
        )
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream),
        )
        await self._session.initialize()
        logger.info(
            "SSE MCP server connected: %s (url=%s)",
            self.config.name,
            self.config.url,
        )

    async def disconnect(self) -> None:
        """Close the SSE connection."""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
        logger.info("SSE MCP server disconnected: %s", self.config.name)

    async def healthcheck(self) -> str:
        """Check if the SSE session is alive."""
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
