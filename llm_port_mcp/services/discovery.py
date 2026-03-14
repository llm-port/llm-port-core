"""Discovers tools from an MCP server and syncs them to the database."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_mcp.services.dao import MCPDao
from llm_port_mcp.services.schema_mapper import compute_schema_hash, to_openai_tool
from llm_port_mcp.services.transport.base import GenericMCPServer, MCPToolDescriptor

logger = logging.getLogger(__name__)


class DiscoveryResult:
    """Result of a tool discovery run."""

    __slots__ = ("added", "updated", "removed", "tools")

    def __init__(self) -> None:
        self.added: int = 0
        self.updated: int = 0
        self.removed: int = 0
        self.tools: list[MCPToolDescriptor] = []


async def discover_tools(
    *,
    server_id: uuid.UUID,
    tenant_id: str,
    adapter: GenericMCPServer,
    session: AsyncSession,
) -> DiscoveryResult:
    """Run tool discovery against a connected MCP server.

    1. Calls ``adapter.list_tools()`` to get upstream tools.
    2. Upserts each tool into the database (detecting schema changes).
    3. Removes tools no longer reported by the server.
    4. Updates the server's ``last_discovery_at`` timestamp.

    Returns a :class:`DiscoveryResult` with counts and descriptors.
    """
    dao = MCPDao(session)
    result = DiscoveryResult()

    # Fetch upstream tools
    descriptors = await adapter.list_tools()
    result.tools = descriptors
    now = datetime.now(timezone.utc)
    active_names: set[str] = set()

    for desc in descriptors:
        active_names.add(desc.qualified_name)
        schema_hash = compute_schema_hash(desc.input_schema)
        openai_schema = to_openai_tool(desc)

        existing = await dao.get_tool_by_qualified_name(
            tenant_id=tenant_id,
            qualified_name=desc.qualified_name,
        )

        if existing is None:
            # New tool
            await dao.upsert_tool(
                server_id=server_id,
                tenant_id=tenant_id,
                upstream_name=desc.upstream_name,
                qualified_name=desc.qualified_name,
                description=desc.description,
                raw_schema_json=desc.input_schema,
                openai_schema_json=openai_schema,
                annotations_json=desc.annotations or None,
                schema_hash=schema_hash,
            )
            result.added += 1
        elif existing.schema_hash != schema_hash:
            # Schema changed — update
            await dao.update_tool(
                existing.id,
                description=desc.description,
                raw_schema_json=desc.input_schema,
                openai_schema_json=openai_schema,
                annotations_json=desc.annotations or None,
                schema_hash=schema_hash,
                version=str(int(existing.version) + 1),
                last_seen_at=now,
            )
            result.updated += 1
        else:
            # Unchanged — just touch last_seen_at
            await dao.update_tool(existing.id, last_seen_at=now)

    # Remove tools that are no longer reported
    if active_names:
        result.removed = await dao.remove_stale_tools(server_id, active_names)
    else:
        # If zero tools discovered, remove all tools for this server
        existing_tools = await dao.list_tools_by_server(server_id)
        for tool in existing_tools:
            await session.delete(tool)
        result.removed = len(existing_tools)

    # Update server discovery timestamp
    await dao.update_server(server_id, last_discovery_at=now, status="active")

    await session.commit()

    logger.info(
        "Discovery for server %s: added=%d updated=%d removed=%d total=%d",
        server_id,
        result.added,
        result.updated,
        result.removed,
        len(descriptors),
    )
    return result
