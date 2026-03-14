"""Manages connections to registered MCP servers."""

from __future__ import annotations

import json
import logging
from typing import Any

from llm_port_mcp.db.models.mcp import MCPServerModel
from llm_port_mcp.services.transport.base import GenericMCPServer, MCPServerConfig

logger = logging.getLogger(__name__)


def _server_config_from_model(server: MCPServerModel) -> MCPServerConfig:
    """Build an MCPServerConfig from a database model."""
    headers = None
    if server.headers_json_encrypted:
        try:
            headers = (
                json.loads(server.headers_json_encrypted)
                if isinstance(server.headers_json_encrypted, str)
                else server.headers_json_encrypted
            )
        except (json.JSONDecodeError, TypeError):
            headers = None

    env = None
    if server.env_json_encrypted:
        try:
            env = (
                json.loads(server.env_json_encrypted)
                if isinstance(server.env_json_encrypted, str)
                else server.env_json_encrypted
            )
        except (json.JSONDecodeError, TypeError):
            env = None

    return MCPServerConfig(
        id=str(server.id),
        name=server.name,
        transport=server.transport,
        url=server.url,
        command=server.command_json,
        args=server.args_json,
        working_dir=server.working_dir,
        headers=headers,
        env=env,
        timeout_sec=server.timeout_sec,
        tool_prefix=server.tool_prefix,
    )


def _create_adapter(config: MCPServerConfig) -> GenericMCPServer:
    """Factory: select transport adapter based on config.transport."""
    if config.transport == "stdio":
        from llm_port_mcp.services.transport.stdio import StdioMCPServer

        return StdioMCPServer(config)
    if config.transport == "sse":
        from llm_port_mcp.services.transport.sse import SSEMCPServer

        return SSEMCPServer(config)
    msg = f"Unsupported transport: {config.transport}"
    raise ValueError(msg)


class MCPConnectionManager:
    """Owns active connections to all registered MCP servers."""

    def __init__(self) -> None:
        self._connections: dict[str, GenericMCPServer] = {}

    @property
    def active_count(self) -> int:
        """Return the number of active connections."""
        return len(self._connections)

    def get(self, server_id: str) -> GenericMCPServer | None:
        """Get an active connection by server ID."""
        return self._connections.get(server_id)

    async def start(self, server: MCPServerModel) -> GenericMCPServer:
        """Create and connect a transport adapter for the given server."""
        server_id = str(server.id)
        if server_id in self._connections:
            logger.warning("Server %s already connected — skipping", server_id)
            return self._connections[server_id]

        config = _server_config_from_model(server)
        adapter = _create_adapter(config)
        await adapter.connect()
        self._connections[server_id] = adapter
        return adapter

    async def start_from_config(self, config: MCPServerConfig) -> GenericMCPServer:
        """Create and connect a transport adapter from config (for testing)."""
        adapter = _create_adapter(config)
        await adapter.connect()
        self._connections[config.id] = adapter
        return adapter

    async def stop(self, server_id: str) -> None:
        """Disconnect and remove a server connection."""
        adapter = self._connections.pop(server_id, None)
        if adapter is not None:
            try:
                await adapter.disconnect()
            except Exception:
                logger.warning(
                    "Error disconnecting server %s",
                    server_id,
                    exc_info=True,
                )

    async def restart(self, server: MCPServerModel) -> GenericMCPServer:
        """Disconnect and reconnect a server."""
        await self.stop(str(server.id))
        return await self.start(server)

    async def stop_all(self) -> None:
        """Disconnect all active servers."""
        server_ids = list(self._connections.keys())
        for server_id in server_ids:
            await self.stop(server_id)

    async def health(self, server_id: str) -> str:
        """Check health of a specific server connection."""
        adapter = self._connections.get(server_id)
        if adapter is None:
            return "disconnected"
        try:
            return await adapter.healthcheck()
        except Exception:
            return "error"

    async def heartbeat_all(
        self,
        redis_pool: Any | None = None,
    ) -> None:
        """Run healthcheck on all active connections and update Redis."""
        for server_id, adapter in list(self._connections.items()):
            try:
                status = await adapter.healthcheck()
            except Exception:
                status = "error"
                logger.debug(
                    "Heartbeat failed for %s",
                    server_id,
                    exc_info=True,
                )

            if redis_pool is not None:
                try:
                    from redis.asyncio import Redis

                    async with Redis(connection_pool=redis_pool) as redis:
                        await redis.set(
                            f"mcp:server:{server_id}:status",
                            status,
                            ex=120,
                        )
                        await redis.set(
                            f"mcp:server:{server_id}:heartbeat",
                            "1",
                            ex=90,
                        )
                except Exception:
                    logger.debug(
                        "Redis heartbeat update failed for %s",
                        server_id,
                        exc_info=True,
                    )
