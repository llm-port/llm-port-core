"""Persistent websocket stream client for backend node control."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.exceptions import ConnectionClosed

from llm_port_node_agent import __version__
from llm_port_node_agent.collectors import collect_inventory, collect_utilization
from llm_port_node_agent.config import AgentConfig
from llm_port_node_agent.dispatcher import CommandDispatcher
from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.state_store import StateStore

log = logging.getLogger(__name__)


class StreamClient:
    """Manage one outbound stream session lifecycle."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        state_store: StateStore,
        dispatcher: CommandDispatcher,
        static_capabilities: dict[str, Any],
        events: EventBuffer,
    ) -> None:
        self._config = config
        self._state = state_store
        self._dispatcher = dispatcher
        self._static_capabilities = static_capabilities
        self._events = events
        self._send_lock = asyncio.Lock()

    async def run(self, *, credential: str) -> None:
        """Open stream and process commands until disconnected."""
        ws_url = self._ws_url()
        headers = {"Authorization": f"Bearer {credential}"}
        log.info("Connecting node stream to %s", ws_url)
        async with websockets.connect(
            ws_url,
            extra_headers=headers,
            open_timeout=self._config.request_timeout_sec,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_size=2**22,
        ) as ws:
            tasks = {
                asyncio.create_task(self._receive_loop(ws), name="receive"),
                asyncio.create_task(self._heartbeat_loop(ws), name="heartbeat"),
                asyncio.create_task(self._inventory_loop(ws), name="inventory"),
                asyncio.create_task(self._event_flush_loop(ws), name="event_flush"),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc

    async def _receive_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            try:
                raw = await ws.recv()
            except ConnectionClosed:
                return
            payload = self._parse_message(raw)
            if payload is None:
                continue
            msg_type = str(payload.get("type") or "").strip().lower()
            if msg_type in {"hello_ack", "commands"}:
                commands = payload.get("commands") if msg_type == "hello_ack" else payload.get("items")
                if isinstance(commands, list):
                    for item in commands:
                        if isinstance(item, dict):
                            await self._handle_command(ws, item)
                node_id = payload.get("node_id")
                if isinstance(node_id, str) and node_id:
                    self._state.state.node_id = node_id
                    self._state.save()
            elif msg_type == "command":
                command = payload.get("command")
                if isinstance(command, dict):
                    await self._handle_command(ws, command)

    async def _heartbeat_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        interval = max(self._config.heartbeat_interval_sec, 5)
        while True:
            await asyncio.sleep(interval)
            status = "healthy"
            if self._state.state.maintenance_mode:
                status = "maintenance"
            elif self._state.state.draining:
                status = "draining"
            await self._send_json(
                ws,
                {
                    "type": "heartbeat",
                    "status": status,
                    "version": __version__,
                    "capabilities": self._static_capabilities,
                },
            )

    async def _inventory_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        interval = max(self._config.inventory_interval_sec, 15)
        while True:
            inventory = await collect_inventory(self._static_capabilities)
            utilization = await collect_utilization()
            await self._send_json(
                ws,
                {
                    "type": "inventory",
                    "inventory": inventory,
                    "utilization": utilization,
                },
            )
            await asyncio.sleep(interval)

    async def _event_flush_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            await asyncio.sleep(5)
            batch = self._events.drain(max_items=100)
            if not batch:
                continue
            await self._send_json(ws, {"type": "event_batch", "events": batch})

    async def _handle_command(
        self,
        ws: websockets.WebSocketClientProtocol,
        command: dict[str, Any],
    ) -> None:
        command_id = str(command.get("id") or "").strip()
        if not command_id:
            return
        command_type = str(command.get("command_type") or "").strip().lower()
        correlation_id = str(command.get("correlation_id") or command_id)
        await self._send_json(
            ws,
            {
                "type": "command_ack",
                "command_id": command_id,
                "message": f"Accepted {command_type}",
                "correlation_id": correlation_id,
            },
        )

        async def emit_progress(progress_payload: dict[str, Any]) -> None:
            await self._send_json(
                ws,
                {
                    "type": "command_progress",
                    "command_id": command_id,
                    "correlation_id": correlation_id,
                    **progress_payload,
                },
            )

        result = await self._dispatcher.handle(command, emit_progress)
        await self._send_json(
            ws,
            {
                "type": "command_result",
                "command_id": command_id,
                "correlation_id": correlation_id,
                **result,
            },
        )

    async def _send_json(self, ws: websockets.WebSocketClientProtocol, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            envelope = dict(payload)
            envelope["seq"] = self._state.next_seq()
            await ws.send(json.dumps(envelope))

    @staticmethod
    def _parse_message(raw: str | bytes) -> dict[str, Any] | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _ws_url(self) -> str:
        parsed = urlparse(self._config.backend_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        host = parsed.netloc
        base_path = parsed.path.rstrip("/")
        return f"{scheme}://{host}{base_path}/api/admin/system/nodes/stream"
