"""In-memory MCP tool catalog cache with version-based invalidation.

The cache stores one ``ToolCatalog`` per tenant.  On each request the
gateway checks whether the cached version matches the current version
in the MCP service (a lightweight HTTP call).  If the version drifts,
the full catalog is re-fetched.

This avoids hammering the MCP service on every chat completion while
ensuring tool additions/removals are picked up within one request.
"""

from __future__ import annotations

import logging
from typing import Any

from llm_port_api.services.gateway.mcp_client import MCPClient, ToolCatalog

logger = logging.getLogger(__name__)

# Prefix used by llm_port_mcp to namespace tool names
MCP_TOOL_PREFIX = "mcp."


class MCPToolCache:
    """Per-tenant in-memory cache of MCP tool definitions."""

    def __init__(self, mcp_client: MCPClient) -> None:
        self._client = mcp_client
        self._cache: dict[str, ToolCatalog] = {}

    async def get_tools(self, tenant_id: str) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool definitions for `tenant_id`.

        Checks the catalog version first; only fetches the full catalog
        when the cached version is stale or missing.
        """
        cached = self._cache.get(tenant_id)
        remote_version = await self._client.get_catalog_version(tenant_id)

        if cached is not None and cached.version == remote_version and remote_version > 0:
            return cached.tools

        catalog = await self._client.get_tool_catalog(tenant_id)
        if catalog.tools:
            self._cache[tenant_id] = catalog
        return catalog.tools

    def invalidate(self, tenant_id: str | None = None) -> None:
        """Drop cache for a tenant or all tenants."""
        if tenant_id:
            self._cache.pop(tenant_id, None)
        else:
            self._cache.clear()
