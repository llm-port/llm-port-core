"""Session-scoped tool availability service.

Builds the effective tool catalog for a session by merging MCP tools
with session execution policy and per-tool overrides.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from llm_port_api.db.dao.gateway_dao import GatewayDAO
from llm_port_api.db.models.gateway import ExecutionMode, ToolRealm
from llm_port_api.services.gateway.mcp_client import MCPClient
from llm_port_api.services.gateway.mcp_tool_cache import MCPToolCache
from llm_port_api.services.gateway.schemas import (
    ExecutionModeEnum,
    ToolAvailabilityDTO,
    ToolAvailabilityResponse,
)

logger = logging.getLogger(__name__)

# Realms permitted per execution mode.
_MODE_ALLOWED_REALMS: dict[str, set[str]] = {
    ExecutionMode.SERVER_ONLY: {
        ToolRealm.SERVER_MANAGED,
        ToolRealm.MCP_REMOTE,
    },
    ExecutionMode.LOCAL_ONLY: {
        ToolRealm.CLIENT_LOCAL,
        ToolRealm.CLIENT_PROXIED,
    },
    ExecutionMode.HYBRID: {
        ToolRealm.SERVER_MANAGED,
        ToolRealm.MCP_REMOTE,
        ToolRealm.CLIENT_LOCAL,
        ToolRealm.CLIENT_PROXIED,
    },
}


class ToolAvailabilityService:
    """Computes the effective tool catalog for a chat session."""

    def __init__(
        self,
        *,
        dao: GatewayDAO,
        mcp_client: MCPClient | None = None,
        mcp_tool_cache: MCPToolCache | None = None,
    ) -> None:
        self._dao = dao
        self._mcp_client = mcp_client
        self._mcp_tool_cache = mcp_tool_cache

    async def get_available_tools(
        self,
        *,
        session_id: uuid.UUID,
        tenant_id: str,
        include_disabled: bool = True,
        include_unavailable: bool = True,
    ) -> ToolAvailabilityResponse:
        """Return the merged effective tool catalog for a session."""

        # 1. Resolve execution mode
        execution_mode = await self._dao.get_session_execution_mode(session_id)
        mode_str = execution_mode.value

        # 2. Load policy (catalog version)
        policy = await self._dao.get_session_execution_policy(session_id)
        catalog_version = policy.catalog_version if policy else 0

        # 3. Load user overrides
        overrides = await self._dao.get_session_tool_overrides(session_id)
        override_map: dict[str, bool] = {o.tool_id: o.enabled for o in overrides}

        # 4. Gather raw tools from all sources
        raw_tools = await self._gather_tools(tenant_id)

        # 5. Compute effective state for each tool
        allowed_realms = _MODE_ALLOWED_REALMS.get(mode_str, set())
        entries: list[ToolAvailabilityDTO] = []

        for tool in raw_tools:
            realm = tool.get("realm", "mcp_remote")
            tool_id = tool["tool_id"]

            # Policy: always allowed for now (future: tenant deny lists)
            policy_allowed = True

            # Mode realm filter
            mode_allows = realm in allowed_realms

            # User override
            user_enabled = override_map.get(tool_id, True)

            # Availability (connectivity/health)
            available = tool.get("available", True)

            # Effective enabled
            effective_enabled = (
                policy_allowed and mode_allows and user_enabled and available
            )

            # Reason for unavailability
            reason = None
            if not effective_enabled:
                if not policy_allowed:
                    reason = "denied_by_policy"
                elif not mode_allows:
                    reason = f"realm_not_allowed_in_{mode_str}_mode"
                elif not user_enabled:
                    reason = "disabled_by_user"
                elif not available:
                    reason = "tool_unavailable"

            # Apply inclusion filters
            if not include_disabled and not effective_enabled:
                continue
            if not include_unavailable and not available:
                continue

            entries.append(
                ToolAvailabilityDTO(
                    tool_id=tool_id,
                    display_name=tool.get("display_name"),
                    description=tool.get("description"),
                    realm=realm,
                    source=tool.get("source", "mcp"),
                    effective_enabled=effective_enabled,
                    policy_allowed=policy_allowed,
                    user_enabled=user_enabled,
                    available=available,
                    availability_reason=reason,
                ),
            )

        return ToolAvailabilityResponse(
            session_id=str(session_id),
            execution_mode=ExecutionModeEnum(mode_str),
            effective_catalog_version=catalog_version,
            tools=entries,
        )

    async def _gather_tools(self, tenant_id: str) -> list[dict[str, Any]]:
        """Collect tools from all registered sources into a flat list."""
        tools: list[dict[str, Any]] = []

        # MCP tools
        if self._mcp_tool_cache is not None:
            try:
                catalog = await self._mcp_tool_cache.get_tools(tenant_id)
                for entry in catalog:
                    qualified_name = entry.get("qualified_name", "")
                    openai_tool = entry.get("openai_tool", {})
                    func_spec = openai_tool.get("function", {})

                    tools.append({
                        "tool_id": qualified_name,
                        "display_name": func_spec.get("name", qualified_name),
                        "description": func_spec.get("description", ""),
                        "realm": entry.get("realm", "mcp_remote"),
                        "source": entry.get("source", "mcp"),
                        "available": True,
                    })
            except Exception:
                logger.debug("Failed to load MCP tools for catalog", exc_info=True)

        # Future: add skills, server_managed, client_local tools here.

        return tools
