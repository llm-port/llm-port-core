"""Client tool broker executor.

Implements the ``ToolExecutor`` protocol by forwarding tool calls to
a connected local agent over WebSocket and awaiting the result.
"""

from __future__ import annotations

import uuid
from typing import Any

from llm_port_api.services.gateway.client_ws import broker_tool_call
from llm_port_api.services.gateway.tool_router import ToolCallResult, ToolRealm


class ClientToolBroker:
    """Executor for client_local and client_proxied realms."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._timeout = timeout

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
        """Broker the tool call to the connected client agent."""
        return await broker_tool_call(
            session_id=session_id,
            tool_id=tool_id,
            arguments=arguments,
            call_id=call_id,
            timeout=self._timeout,
        )
