"""Stdio transport adapter for local/sidecar MCP servers."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from llm_port_mcp.services.transport.base import (
    GenericMCPServer,
    MCPServerConfig,
    MCPToolDescriptor,
)

logger = logging.getLogger(__name__)

# Allowlisted command prefixes for stdio servers.
# Prevents arbitrary command execution.
_ALLOWED_COMMANDS = frozenset({
    "npx",
    "node",
    "python",
    "python3",
    "uvx",
    "uv",
})


def _validate_command(command: list[str]) -> None:
    """Validate that the command is safe to execute."""
    if not command:
        msg = "stdio transport requires a non-empty command"
        raise ValueError(msg)

    executable = command[0].split("/")[-1].split("\\")[-1]

    if executable not in _ALLOWED_COMMANDS:
        msg = (
            f"Command '{executable}' is not in the allowlist. "
            f"Allowed: {sorted(_ALLOWED_COMMANDS)}"
        )
        raise ValueError(msg)


class StdioMCPServer(GenericMCPServer):
    """MCP server connected via stdio (subprocess)."""

    def __init__(self, config: MCPServerConfig) -> None:
        super().__init__(config)
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def connect(self) -> None:
        """Start the subprocess and create an MCP client session."""
        if self.config.command is None:
            msg = "stdio transport requires 'command'"
            raise ValueError(msg)

        _validate_command(self.config.command)

        params = StdioServerParameters(
            command=self.config.command[0],
            args=[*self.config.command[1:], *(self.config.args or [])],
            env=self.config.env,
            cwd=self.config.working_dir,
        )

        self._exit_stack = AsyncExitStack()
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(params),
        )
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream),
        )
        await self._session.initialize()
        logger.info(
            "Stdio MCP server connected: %s (command=%s)",
            self.config.name,
            self.config.command,
        )

    async def disconnect(self) -> None:
        """Terminate the subprocess and close the session."""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
        logger.info("Stdio MCP server disconnected: %s", self.config.name)

    async def healthcheck(self) -> str:
        """Check if the subprocess session is alive."""
        if self._session is None:
            return "disconnected"
        try:
            await self._session.send_ping()
            return "active"
        except Exception:
            return "error"

    async def list_tools(self) -> list[MCPToolDescriptor]:
        """Discover tools from the stdio MCP server."""
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
        """Call a tool on the stdio MCP server."""
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
