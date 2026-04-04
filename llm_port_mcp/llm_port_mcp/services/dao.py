"""Data access layer for MCP server and tool registry."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_mcp.db.models.mcp import MCPServerModel, MCPToolModel


class MCPDao:
    """Data access for MCP servers and tools."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Server operations ─────────────────────────────────────────

    async def create_server(self, **kwargs: Any) -> MCPServerModel:
        """Insert a new MCP server record."""
        server = MCPServerModel(id=uuid.uuid4(), **kwargs)
        self.session.add(server)
        await self.session.flush()
        return server

    async def get_server(self, server_id: uuid.UUID) -> MCPServerModel | None:
        """Fetch a server by ID with tools eagerly loaded."""
        result = await self.session.execute(
            select(MCPServerModel).where(MCPServerModel.id == server_id),
        )
        return result.scalar_one_or_none()

    async def list_servers(
        self,
        *,
        tenant_id: str | None = None,
        transport: str | None = None,
        enabled: bool | None = None,
    ) -> list[MCPServerModel]:
        """List servers with optional filters."""
        query = select(MCPServerModel).order_by(MCPServerModel.name.asc())
        if tenant_id is not None:
            query = query.where(MCPServerModel.tenant_id == tenant_id)
        if transport is not None:
            query = query.where(MCPServerModel.transport == transport)
        if enabled is not None:
            query = query.where(MCPServerModel.enabled == enabled)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_enabled_servers(self) -> list[MCPServerModel]:
        """List all enabled servers (for startup connection)."""
        return await self.list_servers(enabled=True)

    async def update_server(
        self,
        server_id: uuid.UUID,
        **kwargs: Any,
    ) -> MCPServerModel | None:
        """Update server fields."""
        kwargs["updated_at"] = datetime.now(timezone.utc)
        await self.session.execute(
            update(MCPServerModel)
            .where(MCPServerModel.id == server_id)
            .values(**kwargs),
        )
        await self.session.flush()
        return await self.get_server(server_id)

    async def delete_server(self, server_id: uuid.UUID) -> bool:
        """Delete a server and its tools (cascade)."""
        server = await self.get_server(server_id)
        if server is None:
            return False
        await self.session.delete(server)
        await self.session.flush()
        return True

    async def update_server_status(
        self,
        server_id: uuid.UUID,
        status: str,
    ) -> None:
        """Update only the server status field."""
        await self.session.execute(
            update(MCPServerModel)
            .where(MCPServerModel.id == server_id)
            .values(
                status=status,
                updated_at=datetime.now(timezone.utc),
            ),
        )
        await self.session.flush()

    # ── Tool operations ───────────────────────────────────────────

    async def upsert_tool(
        self,
        server_id: uuid.UUID,
        tenant_id: str,
        upstream_name: str,
        qualified_name: str,
        **kwargs: Any,
    ) -> MCPToolModel:
        """Insert or update a tool by qualified_name within a tenant."""
        result = await self.session.execute(
            select(MCPToolModel).where(
                MCPToolModel.tenant_id == tenant_id,
                MCPToolModel.qualified_name == qualified_name,
            ),
        )
        tool = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)

        if tool is not None:
            for key, value in kwargs.items():
                setattr(tool, key, value)
            tool.last_seen_at = now
            await self.session.flush()
            return tool

        tool = MCPToolModel(
            id=uuid.uuid4(),
            server_id=server_id,
            tenant_id=tenant_id,
            upstream_name=upstream_name,
            qualified_name=qualified_name,
            last_seen_at=now,
            **kwargs,
        )
        self.session.add(tool)
        await self.session.flush()
        return tool

    async def list_tools_by_server(
        self,
        server_id: uuid.UUID,
    ) -> list[MCPToolModel]:
        """List all tools belonging to a server."""
        result = await self.session.execute(
            select(MCPToolModel)
            .where(MCPToolModel.server_id == server_id)
            .order_by(MCPToolModel.qualified_name.asc()),
        )
        return list(result.scalars().all())

    async def list_tools_by_tenant(
        self,
        tenant_id: str,
        *,
        enabled_only: bool = True,
    ) -> list[MCPToolModel]:
        """List all tools for a tenant (used for tool catalog)."""
        query = select(MCPToolModel).where(MCPToolModel.tenant_id == tenant_id)
        if enabled_only:
            query = query.where(MCPToolModel.enabled.is_(True))
        result = await self.session.execute(
            query.order_by(MCPToolModel.qualified_name.asc()),
        )
        return list(result.scalars().all())

    async def get_tool(self, tool_id: uuid.UUID) -> MCPToolModel | None:
        """Fetch a tool by ID."""
        result = await self.session.execute(
            select(MCPToolModel).where(MCPToolModel.id == tool_id),
        )
        return result.scalar_one_or_none()

    async def get_tool_by_qualified_name(
        self,
        *,
        tenant_id: str,
        qualified_name: str,
    ) -> MCPToolModel | None:
        """Fetch a tool by its qualified name within a tenant."""
        result = await self.session.execute(
            select(MCPToolModel).where(
                MCPToolModel.tenant_id == tenant_id,
                MCPToolModel.qualified_name == qualified_name,
            ),
        )
        return result.scalar_one_or_none()

    async def update_tool(
        self,
        tool_id: uuid.UUID,
        **kwargs: Any,
    ) -> MCPToolModel | None:
        """Update tool fields."""
        await self.session.execute(
            update(MCPToolModel)
            .where(MCPToolModel.id == tool_id)
            .values(**kwargs),
        )
        await self.session.flush()
        return await self.get_tool(tool_id)

    async def remove_stale_tools(
        self,
        server_id: uuid.UUID,
        active_qualified_names: set[str],
    ) -> int:
        """Remove tools no longer reported by discovery."""
        result = await self.session.execute(
            select(MCPToolModel).where(
                MCPToolModel.server_id == server_id,
                MCPToolModel.qualified_name.notin_(active_qualified_names),
            ),
        )
        stale = list(result.scalars().all())
        for tool in stale:
            await self.session.delete(tool)
        await self.session.flush()
        return len(stale)
