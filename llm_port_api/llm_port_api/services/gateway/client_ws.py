"""WebSocket protocol for local agentic client connections.

Provides a persistent channel between LLM.port and connected local agents
so that client-local tools can be brokered in real time.

Protocol messages (JSON over WS):
  Client -> Server:
    session_init          – authenticate and declare capabilities
    tool_call_result      – return result for a brokered call
    tool_call_error       – report tool execution failure
    tool_registry_update  – hot-update advertised tools
  Server -> Client:
    session_ack           – confirm session initialisation
    tool_call_request     – request the client to execute a tool
    tool_call_cancel      – cancel a pending tool call
    session_close         – server-initiated close
    error                 – protocol-level error
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from llm_port_api.db.dao.gateway_dao import GatewayDAO
from llm_port_api.services.gateway.tool_router import ToolCallResult

logger = logging.getLogger(__name__)

# Default timeout for tool call responses from the client
_TOOL_CALL_TIMEOUT_SEC = 30.0


# ---------------------------------------------------------------------------
# Pending call tracking
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PendingToolCall:
    """Tracks an in-flight tool call brokered to a client."""

    call_id: str
    tool_id: str
    future: asyncio.Future[ToolCallResult]
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Client session state
# ---------------------------------------------------------------------------


@dataclass
class ClientSession:
    """State for one connected local agent."""

    session_id: uuid.UUID
    client_id: str
    tenant_id: str
    websocket: Any  # starlette.websockets.WebSocket
    pending_calls: dict[str, PendingToolCall] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    connected: bool = True


# ---------------------------------------------------------------------------
# Client connection registry (singleton)
# ---------------------------------------------------------------------------


class ClientConnectionRegistry:
    """In-memory registry of connected local agents.

    Keyed by (session_id, client_id) so multiple agents can connect to
    different sessions.
    """

    def __init__(self) -> None:
        self._connections: dict[tuple[uuid.UUID, str], ClientSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, cs: ClientSession) -> None:
        async with self._lock:
            self._connections[(cs.session_id, cs.client_id)] = cs

    async def unregister(self, session_id: uuid.UUID, client_id: str) -> ClientSession | None:
        async with self._lock:
            return self._connections.pop((session_id, client_id), None)

    def get(self, session_id: uuid.UUID, client_id: str) -> ClientSession | None:
        return self._connections.get((session_id, client_id))

    def get_any_for_session(self, session_id: uuid.UUID) -> ClientSession | None:
        """Return the first connected client for a session."""
        for (sid, _), cs in self._connections.items():
            if sid == session_id and cs.connected:
                return cs
        return None


# Global singleton – will be attached to app.state during startup
client_registry = ClientConnectionRegistry()


# ---------------------------------------------------------------------------
# WebSocket message handlers
# ---------------------------------------------------------------------------


async def _handle_session_init(
    ws: Any,
    msg: dict[str, Any],
    dao: GatewayDAO,
) -> ClientSession | None:
    """Process 'session_init' from a client and register capabilities."""
    session_id_str = msg.get("session_id")
    client_id = msg.get("client_id", "")
    tools = msg.get("tools", [])
    tenant_id = msg.get("tenant_id", "default")

    if not session_id_str or not client_id:
        await ws.send_json({
            "type": "error",
            "code": "invalid_session_init",
            "message": "session_id and client_id are required.",
        })
        return None

    try:
        session_id = uuid.UUID(session_id_str)
    except ValueError:
        await ws.send_json({
            "type": "error",
            "code": "invalid_session_id",
            "message": "Invalid session_id format.",
        })
        return None

    # Register capabilities in DB
    tool_records = []
    for t in tools:
        tool_records.append({
            "tool_id": t.get("tool_id", ""),
            "realm": t.get("realm", "client_local"),
            "schema": t.get("schema"),
            "available": True,
        })
    await dao.register_client_capabilities(session_id, client_id, tool_records)

    cs = ClientSession(
        session_id=session_id,
        client_id=client_id,
        tenant_id=tenant_id,
        websocket=ws,
        tools=tools,
    )
    await client_registry.register(cs)
    logger.info("Client %s connected to session %s with %d tools", client_id, session_id, len(tools))

    await ws.send_json({
        "type": "session_ack",
        "session_id": str(session_id),
        "client_id": client_id,
        "tools_registered": len(tool_records),
    })
    return cs


async def _handle_tool_call_result(cs: ClientSession, msg: dict[str, Any]) -> None:
    """Process the result of a brokered tool call from the client."""
    call_id = msg.get("call_id", "")
    pending = cs.pending_calls.pop(call_id, None)
    if pending is None:
        logger.warning("Received result for unknown call_id=%s", call_id)
        return

    result = ToolCallResult(
        call_id=call_id,
        tool_id=pending.tool_id,
        content=msg.get("result", {}).get("content", str(msg.get("result", ""))),
        is_error=msg.get("status", "ok") != "ok",
        latency_ms=int((time.time() - pending.created_at) * 1000),
        realm="client_local",
        executor="ClientToolBroker",
    )
    if not pending.future.done():
        pending.future.set_result(result)


async def _handle_tool_call_error(cs: ClientSession, msg: dict[str, Any]) -> None:
    """Process a tool execution error from the client."""
    call_id = msg.get("call_id", "")
    pending = cs.pending_calls.pop(call_id, None)
    if pending is None:
        logger.warning("Received error for unknown call_id=%s", call_id)
        return

    result = ToolCallResult(
        call_id=call_id,
        tool_id=pending.tool_id,
        content=msg.get("error", "Client tool execution failed."),
        is_error=True,
        latency_ms=int((time.time() - pending.created_at) * 1000),
        realm="client_local",
        executor="ClientToolBroker",
    )
    if not pending.future.done():
        pending.future.set_result(result)


async def _handle_tool_registry_update(
    cs: ClientSession,
    msg: dict[str, Any],
    dao: GatewayDAO,
) -> None:
    """Hot-update the tool list for a connected client."""
    tools = msg.get("tools", [])
    tool_records = [
        {
            "tool_id": t.get("tool_id", ""),
            "realm": t.get("realm", "client_local"),
            "schema": t.get("schema"),
            "available": t.get("available", True),
        }
        for t in tools
    ]
    await dao.register_client_capabilities(cs.session_id, cs.client_id, tool_records)
    cs.tools = tools
    await cs.websocket.send_json({
        "type": "tool_registry_update_ack",
        "tools_registered": len(tool_records),
    })


# ---------------------------------------------------------------------------
# WebSocket endpoint handler
# ---------------------------------------------------------------------------


async def handle_client_websocket(ws: Any, dao: GatewayDAO) -> None:
    """Main WS handler loop for a local agent connection.

    Expected to be called from a FastAPI/Starlette WebSocket route.
    """
    await ws.accept()
    cs: ClientSession | None = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "code": "invalid_json", "message": "Expected JSON."})
                continue

            msg_type = msg.get("type", "")

            if msg_type == "session_init":
                cs = await _handle_session_init(ws, msg, dao)

            elif cs is None:
                await ws.send_json({
                    "type": "error",
                    "code": "not_initialised",
                    "message": "Send session_init first.",
                })

            elif msg_type == "tool_call_result":
                await _handle_tool_call_result(cs, msg)

            elif msg_type == "tool_call_error":
                await _handle_tool_call_error(cs, msg)

            elif msg_type == "tool_registry_update":
                await _handle_tool_registry_update(cs, msg, dao)

            else:
                await ws.send_json({
                    "type": "error",
                    "code": "unknown_message_type",
                    "message": f"Unknown message type: {msg_type}",
                })

    except Exception:
        logger.debug("Client WS disconnected", exc_info=True)
    finally:
        if cs is not None:
            cs.connected = False
            # Cancel pending calls
            for pending in cs.pending_calls.values():
                if not pending.future.done():
                    pending.future.set_result(ToolCallResult(
                        call_id=pending.call_id,
                        tool_id=pending.tool_id,
                        content="Client disconnected before tool call completed.",
                        is_error=True,
                        realm="client_local",
                        executor="ClientToolBroker",
                    ))
            # Mark tools as unavailable
            await dao.mark_client_tools_unavailable(cs.session_id, cs.client_id)
            await client_registry.unregister(cs.session_id, cs.client_id)
            logger.info("Client %s disconnected from session %s", cs.client_id, cs.session_id)


# ---------------------------------------------------------------------------
# Broker function (used by ClientToolBroker executor)
# ---------------------------------------------------------------------------


async def broker_tool_call(
    *,
    session_id: uuid.UUID,
    tool_id: str,
    arguments: dict[str, Any],
    call_id: str,
    timeout: float = _TOOL_CALL_TIMEOUT_SEC,
) -> ToolCallResult:
    """Send a tool_call_request to the connected client and await the result."""
    cs = client_registry.get_any_for_session(session_id)
    if cs is None or not cs.connected:
        return ToolCallResult(
            call_id=call_id,
            tool_id=tool_id,
            content="No client connected for this session.",
            is_error=True,
            realm="client_local",
            executor="ClientToolBroker",
        )

    loop = asyncio.get_running_loop()
    future: asyncio.Future[ToolCallResult] = loop.create_future()
    pending = PendingToolCall(call_id=call_id, tool_id=tool_id, future=future)
    cs.pending_calls[call_id] = pending

    # Send request to client
    await cs.websocket.send_json({
        "type": "tool_call_request",
        "call_id": call_id,
        "session_id": str(session_id),
        "tool_id": tool_id,
        "arguments": arguments,
    })

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        cs.pending_calls.pop(call_id, None)
        return ToolCallResult(
            call_id=call_id,
            tool_id=tool_id,
            content=f"Tool call timed out after {timeout}s.",
            is_error=True,
            latency_ms=int(timeout * 1000),
            realm="client_local",
            executor="ClientToolBroker",
        )
