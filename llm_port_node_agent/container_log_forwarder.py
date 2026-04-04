"""Forward managed container stdout/stderr to Loki.

Periodically tails ``docker logs`` for every tracked workload and
pushes new lines to Loki with per-container labels so that the
frontend can query ``{job="node-container", runtime_id="..."}``
for live container output.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from llm_port_node_agent.loki_client import LokiClient
from llm_port_node_agent.runtimes import ContainerRuntime
from llm_port_node_agent.state_store import StateStore

log = logging.getLogger(__name__)


class ContainerLogForwarder:
    """Tail docker logs for tracked workloads and push to Loki."""

    def __init__(
        self,
        *,
        runtime: ContainerRuntime,
        state_store: StateStore,
        loki: LokiClient,
        host: str,
        interval_sec: int = 5,
    ) -> None:
        self._runtime = runtime
        self._state = state_store
        self._loki = loki
        self._host = host
        self._interval = max(interval_sec, 2)
        # Track last-seen timestamp (RFC 3339) per container to resume tailing
        self._cursors: dict[str, str] = {}

    async def run_forever(self) -> None:
        """Periodically collect container logs and push to Loki."""
        while True:
            try:
                await self._collect_all()
            except Exception:
                log.debug("Container log collection cycle failed.", exc_info=True)
            await asyncio.sleep(self._interval)

    async def _collect_all(self) -> None:
        workloads: dict[str, dict[str, Any]] = dict(self._state.state.workloads)
        if not workloads:
            return

        # Remove cursors for workloads no longer tracked
        stale = set(self._cursors) - set(workloads)
        for key in stale:
            self._cursors.pop(key, None)

        for runtime_id, info in workloads.items():
            container_name = info.get("container_name")
            if not isinstance(container_name, str) or not container_name:
                continue
            try:
                await self._tail_container(runtime_id, info, container_name)
            except Exception:
                log.debug("Failed to tail %s", container_name, exc_info=True)

    async def _tail_container(
        self,
        runtime_id: str,
        info: dict[str, Any],
        container_name: str,
    ) -> None:
        since = self._cursors.get(runtime_id)
        tail_arg: str | None = None
        since_arg: str | None = None
        if since:
            since_arg = since
        else:
            # First collection: grab last 100 lines to bootstrap the view
            tail_arg = "100"

        code, raw = await self._runtime.logs(
            container_name,
            tail=tail_arg,
            since=since_arg,
            timestamps=True,
        )

        if code != 0:
            return

        if not raw.strip():
            return

        lines = raw.splitlines()
        labels = {
            "job": "node-container",
            "host": self._host,
            "container": container_name,
            "runtime_id": runtime_id,
        }

        entries: list[tuple[int, str]] = []
        latest_ts: str | None = None
        for line in lines:
            ts_str, _, text = line.partition(" ")
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ns = int(dt.timestamp() * 1_000_000_000)
            except (ValueError, OverflowError):
                ns = int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)
                text = line  # whole line is the message
            entries.append((ns, text))
            latest_ts = ts_str

        if entries:
            await self._loki.push_streams(labels=labels, entries=entries)

        # Advance cursor so next iteration only gets new lines
        if latest_ts:
            self._cursors[runtime_id] = latest_ts
