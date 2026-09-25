"""The group a user's LLM usage is attributed to.

The backend stores it on the user (``user.usage_group_id``); the gateway stamps
it on every request-log row so usage can be reported per team without joining
across databases. Resolving it must cost the request nothing: answers are
cached in-process for a few minutes, a miss is one indexed query on the
backend database over a small connection pool, concurrent misses for one user
share a single query, and any failure resolves to ``None`` (unattributed)
rather than failing the request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import asyncpg

logger = logging.getLogger(__name__)

Fetch = Callable[[str], Awaitable[str | None]]
Close = Callable[[], Awaitable[None]]

_SQL = 'SELECT usage_group_id::text FROM "user" WHERE id = $1::uuid'


class UsageGroupResolver:
    """``user_id -> usage group id`` with a TTL cache in front of ``fetch``."""

    def __init__(
        self,
        fetch: Fetch,
        *,
        ttl_sec: float = 300.0,
        error_ttl_sec: float = 15.0,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
        close: Close | None = None,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_sec
        self._error_ttl = error_ttl_sec
        self._max = max_entries
        self._clock = clock
        self._close = close
        self._cache: dict[str, tuple[str | None, float]] = {}
        self._inflight: dict[str, asyncio.Future[str | None]] = {}

    async def resolve(self, user_id: str) -> str | None:
        """The user's usage group id, or ``None`` when unattributed or unknown."""
        now = self._clock()
        hit = self._cache.get(user_id)
        if hit is not None and hit[1] > now:
            return hit[0]

        pending = self._inflight.get(user_id)
        if pending is not None:
            return await pending

        fut: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._inflight[user_id] = fut
        try:
            try:
                value = await self._fetch(user_id)
                ttl = self._ttl
            except Exception as exc:  # attribution never fails the request
                logger.warning("Usage group lookup failed for user %s: %s", user_id, exc)
                value, ttl = None, self._error_ttl
            self._remember(user_id, value, now + ttl)
            fut.set_result(value)
            return value
        finally:
            self._inflight.pop(user_id, None)

    def invalidate(self, user_id: str | None = None) -> None:
        """Forget one user, or everyone."""
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)

    async def close(self) -> None:
        if self._close is not None:
            await self._close()

    def _remember(self, user_id: str, value: str | None, expires_at: float) -> None:
        if len(self._cache) >= self._max and user_id not in self._cache:
            now = self._clock()
            for key in [k for k, (_, exp) in self._cache.items() if exp <= now]:
                del self._cache[key]
            if len(self._cache) >= self._max:
                # Still full: drop the entry that expires soonest.
                del self._cache[min(self._cache, key=lambda k: self._cache[k][1])]
        self._cache[user_id] = (value, expires_at)


class BackendUserGroups:
    """``fetch`` over a small asyncpg pool on the backend database."""

    def __init__(self, dsn: str, *, max_size: int = 2, timeout_sec: float = 2.0) -> None:
        self._dsn = dsn
        self._max_size = max_size
        self._timeout = timeout_sec
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def __call__(self, user_id: str) -> str | None:
        pool = await self._get_pool()
        return await pool.fetchval(_SQL, user_id, timeout=self._timeout)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        self._dsn,
                        min_size=0,
                        max_size=self._max_size,
                        timeout=self._timeout,
                    )
        return self._pool


def build_usage_group_resolver(settings: object) -> UsageGroupResolver | None:
    """A resolver over the backend database, or ``None`` when none is configured."""
    backend_db = getattr(settings, "backend_db_base", "")
    if not backend_db:
        return None
    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_pass}"  # type: ignore[attr-defined]
        f"@{settings.db_host}:{settings.db_port}/{backend_db}"  # type: ignore[attr-defined]
    )
    fetch = BackendUserGroups(dsn)
    return UsageGroupResolver(fetch, close=fetch.close)
