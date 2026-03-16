"""In-process stream buffer for SSE reconnection.

Buffers SSE chunks keyed by session ID so that a client that
reloads mid-stream can reconnect and receive the full response.

Uses an asyncio-based in-memory store (no Redis dependency).
Each active stream has:
  - a list of accumulated SSE byte chunks
  - an asyncio.Event that fires on each new chunk
  - a ``done`` flag + cleanup timer

Entries auto-expire after ``ttl_sec`` seconds once the stream ends.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SEC = 120  # keep finished buffers for 2 minutes


@dataclass
class _StreamEntry:
    """Internal bookkeeping for a single session stream."""

    chunks: list[bytes] = field(default_factory=list)
    new_chunk_event: asyncio.Event = field(default_factory=asyncio.Event)
    done: bool = False
    finished_at: float | None = None


class StreamBuffer:
    """Process-local SSE stream buffer.

    Thread-safety: designed for single-process ``asyncio`` servers
    (the standard FastAPI deployment model).  For multi-process
    setups, this would need a Redis Streams backend — left as a
    future enhancement.
    """

    def __init__(self, ttl_sec: int = _DEFAULT_TTL_SEC) -> None:
        self._buffers: dict[str, _StreamEntry] = {}
        self._ttl_sec = ttl_sec
        self._cleanup_task: asyncio.Task[None] | None = None

    # ── producer API ──────────────────────────────────────────

    def start(self, session_id: str) -> None:
        """Register a new active stream for *session_id*."""
        old = self._buffers.get(session_id)
        if old and not old.done:
            logger.warning("Overwriting active stream buffer for session %s", session_id)
        self._buffers[session_id] = _StreamEntry()
        self._ensure_cleanup()
        logger.debug("Stream buffer started for session %s", session_id)

    def push(self, session_id: str, chunk: bytes) -> None:
        """Append an SSE chunk to the buffer and notify subscribers."""
        entry = self._buffers.get(session_id)
        if entry is None or entry.done:
            return
        entry.chunks.append(chunk)
        # Wake up all subscribers waiting for new data
        entry.new_chunk_event.set()
        entry.new_chunk_event = asyncio.Event()

    def finish(self, session_id: str) -> None:
        """Mark the stream as complete."""
        entry = self._buffers.get(session_id)
        if entry is None:
            return
        entry.done = True
        entry.finished_at = time.monotonic()
        # Final wake-up so subscribers exit their loop
        entry.new_chunk_event.set()
        logger.debug("Stream buffer finished for session %s", session_id)

    # ── consumer API ──────────────────────────────────────────

    def is_active(self, session_id: str) -> bool:
        """Return True if there is an active (not finished) stream."""
        entry = self._buffers.get(session_id)
        return entry is not None and not entry.done

    def has_buffer(self, session_id: str) -> bool:
        """Return True if there is any buffer (active or recently finished)."""
        return session_id in self._buffers

    async def subscribe(self, session_id: str) -> AsyncSSEIterator:
        """Return an async iterator that yields buffered + live SSE chunks.

        If the stream is still active, the iterator will block waiting
        for new chunks until the stream finishes.  If already finished,
        it replays all buffered chunks and returns.
        """
        return AsyncSSEIterator(self, session_id)

    # ── housekeeping ──────────────────────────────────────────

    def _ensure_cleanup(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        """Periodically remove expired finished buffers."""
        while self._buffers:
            await asyncio.sleep(30)
            now = time.monotonic()
            expired = [
                sid
                for sid, e in self._buffers.items()
                if e.done and e.finished_at and (now - e.finished_at) > self._ttl_sec
            ]
            for sid in expired:
                del self._buffers[sid]
                logger.debug("Cleaned up expired stream buffer for session %s", sid)
            if not self._buffers:
                break


class AsyncSSEIterator:
    """Async iterator that replays buffered SSE chunks then tails live ones."""

    def __init__(self, buffer: StreamBuffer, session_id: str) -> None:
        self._buffer = buffer
        self._session_id = session_id
        self._cursor = 0

    def __aiter__(self) -> AsyncSSEIterator:
        return self

    async def __anext__(self) -> bytes:
        entry = self._buffer._buffers.get(self._session_id)
        if entry is None:
            raise StopAsyncIteration

        # If there are buffered chunks we haven't sent yet, yield them
        while self._cursor >= len(entry.chunks):
            if entry.done:
                raise StopAsyncIteration
            # Wait for the next chunk to arrive
            event = entry.new_chunk_event
            await event.wait()
            # Re-fetch entry in case it was replaced
            entry = self._buffer._buffers.get(self._session_id)
            if entry is None:
                raise StopAsyncIteration

        chunk = entry.chunks[self._cursor]
        self._cursor += 1
        return chunk
