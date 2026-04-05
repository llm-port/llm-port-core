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

    def __init__(
        self,
        *,
        runtime: ContainerRuntime,
        state_store: StateStore,
        events: EventBuffer,
        advertise_host: str = "127.0.0.1",
        advertise_scheme: str = "http",
    ) -> None:
        self._runtime = runtime
        self._state = state_store
        self._events = events
        self._advertise_host = advertise_host
        self._advertise_scheme = advertise_scheme
        # Track last observed container status per runtime_id to detect transitions.
        self._last_known_status: dict[str, str] = {}

    async def run_forever(self) -> None:
        """Periodically inspect all tracked workloads."""
        # Run an immediate check on startup so reconnecting agents
        # report container state without waiting a full interval.
        await self._check_all_workloads()
        while True:
            await asyncio.sleep(_CHECK_INTERVAL_SEC)
            await self._check_all_workloads()

    async def _check_all_workloads(self) -> None:
        workloads = dict(self._state.state.workloads)
        for runtime_id, info in workloads.items():
            if not isinstance(info, dict):
                continue
            container_name = info.get("container_name")
            if not isinstance(container_name, str) or not container_name:
                continue
            await self._check_container(runtime_id, container_name, info)

    async def _resolve_endpoint(
        self, container_name: str, *, container_port: str = "8000",
    ) -> str | None:
        try:
            host_port = await self._runtime.port(container_name, container_port)
        except Exception:
            return None
        if not host_port:
            return None
        return f"{self._advertise_scheme}://{self._advertise_host}:{host_port}"

    async def _check_container(
        self, runtime_id: str, container_name: str, info: dict[str, Any],
    ) -> None:
        try:
            data = await self._runtime.inspect(container_name, format_="{{json .State}}")
        except Exception:
            log.debug("Failed to inspect container %s", container_name)
            return

        prev_status = self._last_known_status.get(runtime_id, "")

        if data.get("__missing"):
            self._last_known_status[runtime_id] = "missing"
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

        self._last_known_status[runtime_id] = status

        # ── Container is running ──────────────────────────────────
        if status == "running":
            # Emit on transitions (including first check after agent start)
            if prev_status != "running":
                container_port = str(info.get("container_port") or "8000")
                endpoint_url = await self._resolve_endpoint(
                    container_name, container_port=container_port,
                )
                self._events.add(
                    event_type="workload.health.running",
                    severity="info",
                    payload={
                        "runtime_id": runtime_id,
                        "container_name": container_name,
                        "endpoint_url": endpoint_url,
                        "restart_count": restart_count,
                        "previous_status": prev_status or "unknown",
                    },
                    correlation_id=runtime_id,
                )
                log.info(
                    "Workload %s container %s transitioned %s → running",
                    runtime_id,
                    container_name,
                    prev_status or "unknown",
                )

        # ── Container stopped / crashed ───────────────────────────
        elif status in ("exited", "dead", "removing"):
            if prev_status not in (status, "missing"):
                self._events.add(
                    event_type="workload.health.stopped",
                    severity="warning",
                    payload={
                        "runtime_id": runtime_id,
                        "container_name": container_name,
                        "status": status,
                        "exit_code": state.get("ExitCode", -1),
                        "restart_count": restart_count,
                        "previous_status": prev_status or "unknown",
                    },
                    correlation_id=runtime_id,
                )

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


