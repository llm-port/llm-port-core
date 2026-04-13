"""Session-aware tool router.

Dispatches tool calls to the correct executor based on the tool's
execution realm and the session's execution policy.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from llm_port_api.db.dao.gateway_dao import GatewayDAO
from llm_port_api.db.models.gateway import ExecutionMode, ToolRealm
from llm_port_api.services.gateway.policy_engine import PolicyAction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Executor protocol & result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ToolCallResult:
    """Normalised result from any executor."""

    call_id: str
    tool_id: str
    content: str
    is_error: bool = False
    latency_ms: int = 0
    realm: str = ""
    executor: str = ""


class ToolExecutor(Protocol):
    """Interface every executor must satisfy."""

    async def execute(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any],
        call_id: str,
        session_id: uuid.UUID,
        tenant_id: str,
        request_id: str,
    ) -> ToolCallResult: ...


# ---------------------------------------------------------------------------
# Server-managed executor  (wraps existing MCP client)
# ---------------------------------------------------------------------------


class ServerToolExecutor:
    """Executes server-managed and MCP-remote tools."""

    def __init__(self, mcp_client: Any, *, pii_mode_override: str | None = None) -> None:
        self._mcp = mcp_client
        self._pii_mode = pii_mode_override

    async def execute(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any],
        call_id: str,
        session_id: uuid.UUID,
        tenant_id: str,
        request_id: str,
    ) -> ToolCallResult:
        t0 = time.perf_counter()
        is_error = False
        content = ""
        try:
            result = await self._mcp.call_tool(
                qualified_name=tool_id,
                arguments=arguments,
                tenant_id=tenant_id,
                request_id=request_id,
                pii_mode_override=self._pii_mode,
            )
            content = result.content
            is_error = result.is_error
        except Exception as exc:
            is_error = True
            content = f"Tool call failed: {exc}"
        latency = int((time.perf_counter() - t0) * 1000)
        return ToolCallResult(
            call_id=call_id,
            tool_id=tool_id,
            content=content,
            is_error=is_error,
            latency_ms=latency,
            realm=ToolRealm.SERVER_MANAGED,
            executor="ServerToolExecutor",
        )


# ---------------------------------------------------------------------------
# Route decision
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RouteDecision:
    """Captures the routing decision for audit/trace."""

    call_id: str
    tool_id: str
    realm: str
    executor: str
    session_id: str
    policy_decision: str  # "allow" | "deny" | "approval_required"
    denial_reason: str | None = None


# ---------------------------------------------------------------------------
# Tool Router
# ---------------------------------------------------------------------------

# Which realms are allowed per execution mode
_MODE_REALMS: dict[str, set[str]] = {
    ExecutionMode.SERVER_ONLY: {ToolRealm.SERVER_MANAGED, ToolRealm.MCP_REMOTE},
    ExecutionMode.LOCAL_ONLY: {ToolRealm.CLIENT_LOCAL, ToolRealm.CLIENT_PROXIED},
    ExecutionMode.HYBRID: {
        ToolRealm.SERVER_MANAGED,
        ToolRealm.MCP_REMOTE,
        ToolRealm.CLIENT_LOCAL,
        ToolRealm.CLIENT_PROXIED,
    },
}


@dataclass(slots=True)
class ToolRouterConfig:
    """Pluggable executors for the router."""

    server_executor: ServerToolExecutor | None = None
    client_broker: ToolExecutor | None = None
    policy_engine: Any | None = None


class ToolRouter:
    """Routes tool calls to the correct executor based on realm + policy."""

    def __init__(
        self,
        *,
        dao: GatewayDAO,
        config: ToolRouterConfig,
    ) -> None:
        self._dao = dao
        self._cfg = config
        # Build realm -> executor mapping
        self._executors: dict[str, ToolExecutor | None] = {
            ToolRealm.SERVER_MANAGED: config.server_executor,
            ToolRealm.MCP_REMOTE: config.server_executor,
            ToolRealm.CLIENT_LOCAL: config.client_broker,
            ToolRealm.CLIENT_PROXIED: config.client_broker,
        }

    def _resolve_realm(self, tool_id: str) -> str:
        """Infer realm from the tool namespace prefix."""
        if tool_id.startswith("client."):
            return ToolRealm.CLIENT_LOCAL
        if tool_id.startswith("mcp."):
            return ToolRealm.MCP_REMOTE
        return ToolRealm.SERVER_MANAGED

    def _resolve_hybrid_realm(
        self,
        tool_id: str,
        preferred_realm: str,
        hybrid_preference: str | None,
    ) -> str:
        """For hybrid mode, choose the best realm when multiple are available.

        ``hybrid_preference`` can be ``"prefer_server"`` or ``"prefer_client"``.
        The fallback order is: preferred realm → opposite realm → original.
        """
        if hybrid_preference == "prefer_server":
            order = [ToolRealm.SERVER_MANAGED, ToolRealm.MCP_REMOTE, ToolRealm.CLIENT_LOCAL]
        elif hybrid_preference == "prefer_client":
            order = [ToolRealm.CLIENT_LOCAL, ToolRealm.CLIENT_PROXIED, ToolRealm.SERVER_MANAGED]
        else:
            return preferred_realm

        # If the preferred realm has an executor, keep it
        if self._executors.get(preferred_realm) is not None:
            return preferred_realm

        # Otherwise follow preference order
        for realm in order:
            if self._executors.get(realm) is not None:
                return realm
        return preferred_realm

    async def route(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any],
        call_id: str,
        session_id: uuid.UUID,
        tenant_id: str,
        request_id: str,
    ) -> ToolCallResult:
        """Route a single tool call to the correct executor."""
        realm = self._resolve_realm(tool_id)
        mode = await self._dao.get_session_execution_mode(session_id)
        allowed_realms = _MODE_REALMS.get(mode.value, set())

        # Hybrid mode: resolve best realm based on preference
        if mode == ExecutionMode.HYBRID:
            policy = await self._dao.get_session_execution_policy(session_id)
            hybrid_pref = policy.hybrid_preference if policy else None
            realm = self._resolve_hybrid_realm(tool_id, realm, hybrid_pref)

        # Realm allowed check
        if realm not in allowed_realms:
            logger.warning(
                "Tool %s realm=%s blocked by mode=%s", tool_id, realm, mode.value,
            )
            return ToolCallResult(
                call_id=call_id,
                tool_id=tool_id,
                content=f"Tool '{tool_id}' realm '{realm}' is not allowed in '{mode.value}' mode.",
                is_error=True,
                realm=realm,
                executor="ToolRouter",
            )

        # Policy engine check
        if self._cfg.policy_engine is not None:
            decision = await self._cfg.policy_engine.evaluate(
                tool_id=tool_id,
                arguments=arguments,
                session_id=session_id,
                tenant_id=tenant_id,
                realm=realm,
            )
            if decision.action == PolicyAction.DENY:
                return ToolCallResult(
                    call_id=call_id,
                    tool_id=tool_id,
                    content=f"Tool '{tool_id}' denied by policy: {decision.reason}",
                    is_error=True,
                    realm=realm,
                    executor="PolicyEngine",
                )
            if decision.action == PolicyAction.APPROVAL_REQUIRED:
                return ToolCallResult(
                    call_id=call_id,
                    tool_id=tool_id,
                    content=f"Tool '{tool_id}' requires approval: {decision.reason}",
                    is_error=True,
                    realm=realm,
                    executor="PolicyEngine",
                )

        executor = self._executors.get(realm)
        if executor is None:
            # Fallback: try alternate realm if in hybrid mode
            if mode == ExecutionMode.HYBRID:
                for fallback_realm in allowed_realms:
                    if self._executors.get(fallback_realm) is not None:
                        logger.info(
                            "Falling back from %s to %s for %s",
                            realm, fallback_realm, tool_id,
                        )
                        executor = self._executors[fallback_realm]
                        realm = fallback_realm
                        break

            if executor is None:
                return ToolCallResult(
                    call_id=call_id,
                    tool_id=tool_id,
                    content=f"No executor registered for realm '{realm}'.",
                    is_error=True,
                    realm=realm,
                    executor="ToolRouter",
                )

        result = await executor.execute(
            tool_id=tool_id,
            arguments=arguments,
            call_id=call_id,
            session_id=session_id,
            tenant_id=tenant_id,
            request_id=request_id,
        )

        # Fallback on executor failure in hybrid mode
        if result.is_error and mode == ExecutionMode.HYBRID:
            for fallback_realm in allowed_realms:
                if fallback_realm == realm:
                    continue
                fb_executor = self._executors.get(fallback_realm)
                if fb_executor is not None:
                    logger.info(
                        "Primary executor failed for %s, falling back to %s",
                        tool_id, fallback_realm,
                    )
                    result = await fb_executor.execute(
                        tool_id=tool_id,
                        arguments=arguments,
                        call_id=call_id,
                        session_id=session_id,
                        tenant_id=tenant_id,
                        request_id=request_id,
                    )
                    if not result.is_error:
                        break

        return result

    async def route_batch(
        self,
        *,
        tool_calls: list[dict[str, Any]],
        session_id: uuid.UUID,
        tenant_id: str,
        request_id: str,
    ) -> list[ToolCallResult]:
        """Route multiple tool calls sequentially."""
        results: list[ToolCallResult] = []
        for tc in tool_calls:
            func = tc.get("function", {})
            tool_id = func.get("name", "")
            import json  # noqa: PLC0415

            try:
                arguments = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            result = await self.route(
                tool_id=tool_id,
                arguments=arguments,
                call_id=tc.get("id", ""),
                session_id=session_id,
                tenant_id=tenant_id,
                request_id=request_id,
            )
            results.append(result)
        return results
