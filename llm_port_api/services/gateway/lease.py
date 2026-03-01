from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ResponseError

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
        """Attempt to acquire a lease for an instance.

        Uses an atomic Lua script.  If Redis scripting is unavailable the
        lease is **refused** rather than falling back to non-atomic
        commands, which would allow over-allocation under concurrency.
        """
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
                log.error(
                    "Redis Lua scripting unavailable - lease refused for "
                    "instance %s (request %s). Ensure Redis EVAL is enabled.",
                    instance_id,
                    request_id,
                )
                return False

    async def release(self, *, instance_id: uuid.UUID | str, request_id: str) -> None:
        """Release a previously-acquired lease.

        If the Lua script fails the lease key will auto-expire via TTL.
        """
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
                log.warning(
                    "Redis Lua scripting unavailable - lease for request %s "
                    "will auto-expire via TTL (%ds).",
                    request_id,
                    self.ttl_sec,
                )
