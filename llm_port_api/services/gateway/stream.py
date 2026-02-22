from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from llm_port_api.services.gateway.usage import Usage, usage_from_payload


@dataclass(slots=True)
class StreamStats:
    """Collected streaming stats."""

    ttft_ms: int | None = None
    usage: Usage = field(default_factory=lambda: Usage(None, None, None))


async def wrap_sse_stream(
    stream: AsyncIterator[bytes],
    *,
    on_first_data: Callable[[], None] | None = None,
) -> tuple[AsyncIterator[bytes], StreamStats]:
    """
    Wrap a raw SSE stream and capture first-token and usage information.

    The wrapped iterator preserves chunk bytes exactly.
    """
    stats = StreamStats()
    started_at = time.perf_counter()
    first_event_seen = False

    async def _iter() -> AsyncIterator[bytes]:
        nonlocal first_event_seen
        async for chunk in stream:
            if not first_event_seen and _contains_data_event(chunk):
                first_event_seen = True
                stats.ttft_ms = int((time.perf_counter() - started_at) * 1000)
                if on_first_data:
                    on_first_data()
            _extract_usage_from_chunk(chunk, stats)
            yield chunk

    return _iter(), stats


def _contains_data_event(chunk: bytes) -> bool:
    text = chunk.decode("utf-8", errors="ignore")
    return "data:" in text and "[DONE]" not in text


def _extract_usage_from_chunk(chunk: bytes, stats: StreamStats) -> None:
    text = chunk.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            parsed: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            continue
        usage = usage_from_payload(parsed)
        if usage.total_tokens is not None:
            stats.usage = usage
