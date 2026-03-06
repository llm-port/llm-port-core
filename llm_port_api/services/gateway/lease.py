from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_port_api.services.cache.protocol import CacheBackend

log = logging.getLogger(__name__)

_ACQUIRE_LUA = """
local active_key = KEYS[1]
local lease_key = KEYS[2]
local request_id = ARGV[1]
local max_concurrency = tonumber(ARGV[2])
local ttl_sec = tonumber(ARGV[3])

local current = tonumber(redis.call('GET', active_key) or '0')
if current >= max_concurrency then
  return 0
end

redis.call('INCR', active_key)
redis.call('SET', lease_key, request_id, 'EX', ttl_sec)
return 1
"""

_RELEASE_LUA = """
local active_key = KEYS[1]
local lease_key = KEYS[2]

if redis.call('EXISTS', lease_key) == 1 then
  redis.call('DEL', lease_key)
  local current = tonumber(redis.call('GET', active_key) or '0')
  if current > 0 then
    redis.call('DECR', active_key)
  end
  return 1
end
return 0
"""


class LeaseManager:
    """Distributed lease manager for per-instance concurrency caps."""

    def __init__(self, cache: CacheBackend, ttl_sec: int) -> None:
        self.cache = cache
        self.ttl_sec = ttl_sec

    @staticmethod
    def active_key(instance_id: uuid.UUID | str) -> str:
        return f"llm:active:{instance_id}"

    @staticmethod
    def lease_key(request_id: str) -> str:
        return f"llm:lease:{request_id}"

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
            2,
            self.active_key(instance_id),
            self.lease_key(request_id),
            request_id,
            str(max(max_concurrency, 1)),
            str(self.ttl_sec),
        )
        return result == "1"

    async def release(self, *, instance_id: uuid.UUID | str, request_id: str) -> None:
        """Release a previously-acquired lease."""
        await self.cache.eval_lua(
            _RELEASE_LUA,
            2,
            self.active_key(instance_id),
            self.lease_key(request_id),
        )
