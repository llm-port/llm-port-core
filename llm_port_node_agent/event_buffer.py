"""Buffered node events for batched stream forwarding."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class EventBuffer:
    """In-memory event queue drained by stream flush loop."""

    _items: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        *,
        event_type: str,
        severity: str = "info",
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Append one normalized event payload."""
        self._items.append(
            {
                "event_type": event_type,
                "severity": severity,
                "correlation_id": correlation_id,
                "payload": payload or {},
                "ts": datetime.now(tz=UTC).isoformat(),
            },
        )

    def drain(self, *, max_items: int = 100) -> list[dict[str, Any]]:
        """Pop up to max_items events."""
        if not self._items:
            return []
        chunk = self._items[:max_items]
        self._items = self._items[max_items:]
        return chunk
