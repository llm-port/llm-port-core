"""Async Loki push client using httpx.

Batches :class:`~log_collector.LogEntry` items and pushes them to
``/loki/api/v1/push`` in the Loki JSON format.  Designed to be
driven by an async loop that calls :meth:`add` for each entry and
:meth:`flush` periodically.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from llm_port_node_agent.log_collector import LogEntry

log = logging.getLogger(__name__)


class LokiClient:
    """Batching Loki push client.

    Parameters
    ----------
    loki_url:
        Base URL of the Loki instance (e.g. ``http://10.0.0.1:3100``).
    labels:
        Static labels applied to every stream pushed.
    verify_tls:
        Whether to verify HTTPS certificates.
    """

    PUSH_PATH = "/loki/api/v1/push"

    def __init__(
        self,
        *,
        loki_url: str,
        labels: dict[str, str],
        verify_tls: bool = True,
    ) -> None:
        self._url = loki_url.rstrip("/") + self.PUSH_PATH
        self._labels = labels
        self._buffer: list[LogEntry] = []
        self._http = httpx.AsyncClient(
            timeout=10,
            verify=verify_tls,
        )

    async def close(self) -> None:
        await self._http.aclose()

    def add(self, entry: LogEntry) -> None:
        """Buffer a single log entry for the next flush."""
        self._buffer.append(entry)

    def add_many(self, entries: list[LogEntry]) -> None:
        """Buffer multiple log entries."""
        self._buffer.extend(entries)

    @property
    def pending(self) -> int:
        return len(self._buffer)

    async def flush(self) -> bool:
        """Push buffered entries to Loki and clear the buffer.

        Returns ``True`` on success, ``False`` on failure (entries
        are discarded to prevent unbounded growth).
        """
        if not self._buffer:
            return True

        entries = self._buffer
        self._buffer = []

        # Group entries by level so Loki gets separate streams per level
        streams: dict[str, list[list[str]]] = {}
        for entry in entries:
            level = entry.level
            if level not in streams:
                streams[level] = []
            streams[level].append([str(entry.timestamp_ns), entry.line])

        payload: dict[str, Any] = {
            "streams": [
                {
                    "stream": {**self._labels, "level": level},
                    "values": values,
                }
                for level, values in streams.items()
            ],
        }

        try:
            resp = await self._http.post(
                self._url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code in {200, 204}:
                log.debug("Pushed %d log entries to Loki.", len(entries))
                return True
            log.warning(
                "Loki push returned %d: %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        except httpx.HTTPError as exc:
            log.warning("Loki push failed: %s", exc)
            return False
