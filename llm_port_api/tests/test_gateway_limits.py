import uuid

import pytest
from redis.asyncio import ConnectionPool

from llm_port_api.services.cache.redis import RedisCache
from llm_port_api.services.gateway.lease import LeaseManager
from llm_port_api.services.gateway.ratelimit import RateLimiter


@pytest.mark.anyio
async def test_lease_respects_max_concurrency(fake_redis_pool: ConnectionPool) -> None:
    cache = RedisCache(fake_redis_pool)
    lease = LeaseManager(cache, ttl_sec=30)
    instance_id = uuid.uuid4()
    acquired_first = await lease.try_acquire(
        instance_id=instance_id,
        request_id="req-1",
        max_concurrency=1,
    )
    acquired_second = await lease.try_acquire(
        instance_id=instance_id,
        request_id="req-2",
        max_concurrency=1,
    )
    assert acquired_first is True
    assert acquired_second is False
    await lease.release(instance_id=instance_id, request_id="req-1")
    acquired_third = await lease.try_acquire(
        instance_id=instance_id,
        request_id="req-3",
        max_concurrency=1,
    )
    assert acquired_third is True


@pytest.mark.anyio
async def test_rate_limiter_rpm_and_tpm(fake_redis_pool: ConnectionPool) -> None:
    cache = RedisCache(fake_redis_pool)
    limiter = RateLimiter(cache)
    rpm_1 = await limiter.check_rpm(tenant_id="tenant-x", limit=1)
    rpm_2 = await limiter.check_rpm(tenant_id="tenant-x", limit=1)
    assert rpm_1 is not None and rpm_1.allowed
    assert rpm_2 is not None and not rpm_2.allowed

    tpm_1 = await limiter.check_tpm(tenant_id="tenant-y", tokens=3, limit=5)
    tpm_2 = await limiter.check_tpm(tenant_id="tenant-y", tokens=3, limit=5)
    assert tpm_1 is not None and tpm_1.allowed
    assert tpm_2 is not None and not tpm_2.allowed
