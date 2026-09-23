"""Reading a streamed chat completion that may call tools.

A streamed answer that calls a tool sends the call in pieces: the name in one
chunk, the arguments spread over the next ones (``delta.tool_calls``, keyed by
``index``). The gateway runs its own tools between rounds, so it reads each
round's events, puts the calls together, and passes only the answer's text
on to the client.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

DONE = "[DONE]"


async def sse_events(stream: AsyncIterator[bytes]) -> AsyncIterator[dict[str, Any] | str]:
    """The events of an SSE stream: each ``data:`` payload parsed, or ``DONE``."""
    buffer = b""
    async for chunk in stream:
        buffer += chunk
        while b"\n\n" in buffer:
            event, buffer = buffer.split(b"\n\n", 1)
            parsed = _parse(event)
            if parsed is not None:
                yield parsed
    if buffer.strip():
        parsed = _parse(buffer)
        if parsed is not None:
            yield parsed


def _parse(event: bytes) -> dict[str, Any] | str | None:
    for raw_line in event.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == DONE:
            return DONE
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def sse(body: dict[str, Any] | str) -> bytes:
    """One SSE event."""
    data = body if isinstance(body, str) else json.dumps(body)
    return f"data: {data}\n\n".encode()


class ToolCalls:
    """Tool calls put together from a stream's ``delta.tool_calls`` pieces."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}

    def add(self, pieces: list[dict[str, Any]]) -> None:
        for piece in pieces:
            call = self._calls.setdefault(
                piece.get("index", len(self._calls)),
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if piece.get("id"):
                call["id"] = piece["id"]
            function = piece.get("function") or {}
            if function.get("name"):
                call["function"]["name"] += function["name"]
            if function.get("arguments"):
                call["function"]["arguments"] += function["arguments"]

    def __bool__(self) -> bool:
        return bool(self._calls)

    def calls(self) -> list[dict[str, Any]]:
        return [self._calls[i] for i in sorted(self._calls)]

    def as_deltas(self) -> list[dict[str, Any]]:
        """The calls as one delta, for a client that runs them itself."""
        return [
            {"index": i, "id": c["id"], "type": "function", "function": c["function"]}
            for i, c in enumerate(self.calls())
        ]
