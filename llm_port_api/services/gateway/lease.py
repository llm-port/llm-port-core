from __future__ import annotations

import uuid
from typing import Any, cast

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ResponseError

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

    def __init__(self, pool: ConnectionPool, ttl_sec: int) -> None:
        self.pool = pool
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
        """Attempt to acquire a lease for an instance."""
        async with Redis(connection_pool=self.pool) as redis:
            try:
                eval_result = redis.eval(
                    _ACQUIRE_LUA,
                    2,
                    self.active_key(instance_id),
                    self.lease_key(request_id),
                    request_id,
                    str(max(max_concurrency, 1)),
                    str(self.ttl_sec),
                )
                result = await cast(Any, eval_result)
                return str(result) == "1"
            except ResponseError:
                active_key = self.active_key(instance_id)
                lease_key = self.lease_key(request_id)
                current_raw = await redis.get(active_key)
                current = int(current_raw) if current_raw else 0
                if current >= max(max_concurrency, 1):
                    return False
                await redis.incr(active_key)
                await redis.set(lease_key, request_id, ex=self.ttl_sec)
                return True

    async def release(self, *, instance_id: uuid.UUID | str, request_id: str) -> None:
        """Release a previously-acquired lease."""
        async with Redis(connection_pool=self.pool) as redis:
            try:
                eval_result = redis.eval(
                    _RELEASE_LUA,
                    2,
                    self.active_key(instance_id),
                    self.lease_key(request_id),
                )
                await cast(Any, eval_result)
            except ResponseError:
                active_key = self.active_key(instance_id)
                lease_key = self.lease_key(request_id)
                if not await redis.exists(lease_key):
                    return
                await redis.delete(lease_key)
                current_raw = await redis.get(active_key)
                current = int(current_raw) if current_raw else 0
                if current > 0:
                    await redis.decr(active_key)
