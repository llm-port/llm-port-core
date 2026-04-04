"""CacheBackend protocol — structural typing for cache adapters."""

from __future__ import annotations

from typing import Protocol, Sequence


class CacheBackend(Protocol):
    """Minimal cache / distributed-state interface.

    Implementations
    ---------------
    * ``NoOpCache``  — all reads return *None*, writes are no-ops (Core default).
    * ``RedisCache``  — backed by ``redis.asyncio.ConnectionPool``.
    * ``PostgresCache`` — (future) for teams that want caching without Redis.
    """

    async def get(self, key: str) -> bytes | None:
        """Return raw value or *None* if missing."""
        ...

    async def set(self, key: str, value: bytes | str, *, ttl_sec: int = 0) -> None:
        """Store *value* under *key* with optional TTL (seconds, 0 = no expiry)."""
        ...

    async def incr(self, key: str, amount: int = 1, *, ttl_sec: int = 0) -> int:
        """Atomically increment *key* by *amount* and return the new value.

        When the key is created for the first time, a TTL is applied if
        *ttl_sec* > 0.
        """
        ...

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        """Return values for multiple keys in order."""
        ...

    async def eval_lua(
        self,
        script: str,
        num_keys: int,
        *args: str,
    ) -> str:
        """Execute a Lua script and return its result as a string.

        Used by ``LeaseManager`` for atomic acquire / release.
        ``NoOpCache`` returns a configurable default (``"1"`` = succeed).
        """
        ...

    async def close(self) -> None:
        """Release resources (connection pools, etc.)."""
        ...
