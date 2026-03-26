"""Periodic workload health checking and crash loop detection."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.runtimes import ContainerRuntime
from llm_port_node_agent.state_store import StateStore

log = logging.getLogger(__name__)

_CHECK_INTERVAL_SEC = 30
_CRASH_RESTART_THRESHOLD = 5


class HealthSupervisor:
    """Monitor tracked workload containers for health and crash loops."""

    def __init__(self, *, runtime: ContainerRuntime, state_store: StateStore, events: EventBuffer) -> None:
        self._runtime = runtime
        self._state = state_store
        self._events = events

    async def run_forever(self) -> None:
        """Periodically inspect all tracked workloads."""
        while True:
            await asyncio.sleep(_CHECK_INTERVAL_SEC)
            workloads = dict(self._state.state.workloads)
            for runtime_id, info in workloads.items():
                if not isinstance(info, dict):
                    continue
                container_name = info.get("container_name")
                if not isinstance(container_name, str) or not container_name:
                    continue
                await self._check_container(runtime_id, container_name)

    async def _check_container(self, runtime_id: str, container_name: str) -> None:
        try:
            data = await self._runtime.inspect(container_name, format_="{{json .State}}")
        except Exception:
            log.debug("Failed to inspect container %s", container_name)
            return

        if data.get("__missing"):
            self._events.add(
                event_type="workload.health.missing",
                severity="warning",
                payload={"runtime_id": runtime_id, "container_name": container_name},
                correlation_id=runtime_id,
            )
            return

        state = data

        status = str(state.get("Status", "")).lower()
        restart_count = int(state.get("RestartCount", 0))
        health = state.get("Health", {})
        health_status = str(health.get("Status", "")).lower() if isinstance(health, dict) else ""

        if restart_count >= _CRASH_RESTART_THRESHOLD:
            self._events.add(
                event_type="workload.health.crash_loop",
                severity="error",
                payload={
                    "runtime_id": runtime_id,
                    "container_name": container_name,
                    "restart_count": restart_count,
                    "status": status,
                },
                correlation_id=runtime_id,
            )

        if health_status == "unhealthy":
            self._events.add(
                event_type="workload.health.unhealthy",
                severity="warning",
                payload={
                    "runtime_id": runtime_id,
                    "container_name": container_name,
                    "health_status": health_status,
                },
                correlation_id=runtime_id,
            )


