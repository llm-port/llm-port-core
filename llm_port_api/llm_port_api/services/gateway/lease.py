from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_port_api.services.cache.protocol import CacheBackend

log = logging.getLogger(__name__)

# Each instance's requests in flight are a sorted set of request ids, scored
# by when they started. A slot is free again when its request releases it,
# or when the lease runs out -- whichever comes first.
#
# It used to be a plain counter, decremented on release only while the
# request's lease key still existed. A request that outlived its lease (any
# chat that hung past 90 s, as they did while a cluster's head was down)
# therefore never gave its slot back: on the dev gateway the chat model had
# leaked 8 of its 16 slots, and 22 of 30 simultaneous requests were refused
# with "no free capacity" while nothing at all was running.
_ACQUIRE_LUA = """
local key = KEYS[1]
local request_id = ARGV[1]
local max_concurrency = tonumber(ARGV[2])
local ttl_sec = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - ttl_sec)
if redis.call('ZCARD', key) >= max_concurrency then
  return 0
end
redis.call('ZADD', key, now, request_id)
redis.call('EXPIRE', key, math.ceil(ttl_sec) * 2)
return 1
"""

_RELEASE_LUA = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""

_COUNT_LUA = """
local ttl_sec = tonumber(ARGV[1])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - ttl_sec)
return redis.call('ZCARD', KEYS[1])
"""


class LeaseManager:
    """Distributed lease manager for per-instance concurrency caps."""

    def __init__(self, cache: CacheBackend, ttl_sec: int) -> None:
        self.cache = cache
        self.ttl_sec = ttl_sec

    @staticmethod
    def active_key(instance_id: uuid.UUID | str) -> str:
        # A new name: the old counters (``llm:active:*``) are plain strings,
        # and reusing their keys as sets would fail with WRONGTYPE.
        return f"llm:inflight:{instance_id}"

    async def try_acquire(
        self,
        *,
        instance_id: uuid.UUID | str,
        request_id: str,
        max_concurrency: int,
    ) -> bool:
        """Attempt to acquire a lease for an instance.

        Uses an atomic Lua script via ``CacheBackend.eval_lua``.
        With ``NoOpCache`` the lease always succeeds (fail-open).
        """
        result = await self.cache.eval_lua(
            _ACQUIRE_LUA,
            1,
            self.active_key(instance_id),
            request_id,
            str(max(max_concurrency, 1)),
            str(self.ttl_sec),
        )
        return result == "1"

    async def release(self, *, instance_id: uuid.UUID | str, request_id: str) -> None:
        """Release a previously-acquired lease."""
        await self.cache.eval_lua(_RELEASE_LUA, 1, self.active_key(instance_id), request_id)

    async def in_flight(self, instance_id: uuid.UUID | str) -> int:
        """Requests holding a slot on *instance_id* now, expired ones dropped."""
        result = await self.cache.eval_lua(_COUNT_LUA, 1, self.active_key(instance_id), str(self.ttl_sec))
        try:
            return int(result)
        except (TypeError, ValueError):
            return 0
