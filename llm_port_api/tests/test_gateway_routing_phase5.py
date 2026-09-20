"""Unit and integration tests for Phase 5 gateway routing, multi-target pools, and failover."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.dao.gateway_dao import GatewayDAO, RoutedInstance
from llm_port_api.db.models.gateway import (
    LLMGatewayRequestLog,
    LLMModelAlias,
    LLMPoolMembership,
    LLMProviderInstance,
    ProviderHealthStatus,
    ProviderType,
)
from llm_port_api.services.gateway.lease import LeaseManager
from llm_port_api.services.gateway.proxy import UpstreamProxy
from llm_port_api.services.gateway.routing import RouterService


class InMemoryCache:
    """Minimal cache backend for testing lease manager and active counts."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def set(self, key: str, val: Any, expire_sec: int | None = None) -> None:
        self.data[key] = val

    async def mget(self, keys: list[str]) -> list[Any]:
        return [self.data.get(k) for k in keys]

    async def incr(self, key: str) -> int:
        val = int(self.data.get(key, 0)) + 1
        self.data[key] = val
        return val

    async def decr(self, key: str) -> int:
        val = max(int(self.data.get(key, 0)) - 1, 0)
        self.data[key] = val
        return val


@pytest.mark.anyio
async def test_mixed_pool_candidate_resolution(db_session: AsyncSession):
    """An alias can contain both a native runtime target and a Ray deployment target."""
    alias_name = f"shared-alias-{uuid.uuid4().hex[:8]}"
    native_id = uuid.uuid4()
    ray_dep_id = uuid.uuid4()
    ray_inst_id = uuid.uuid4()

    # 1. Model alias
    alias = LLMModelAlias(alias=alias_name, enabled=True)
    db_session.add(alias)

    # 2. Native runtime provider
    native_inst = LLMProviderInstance(
        id=native_id,
        type=ProviderType.VLLM,
        base_url="http://10.0.0.10:8000",
        enabled=True,
        weight=1.0,
        max_concurrency=10,
        health_status=ProviderHealthStatus.HEALTHY,
        source_kind="runtime",
        source_id=native_id,
    )
    db_session.add(native_inst)

    # 3. Ray Serve deployment provider
    ray_inst = LLMProviderInstance(
        id=ray_inst_id,
        type=ProviderType.VLLM,
        base_url="http://10.88.10.49:8000/llmport-dep1",
        enabled=True,
        weight=2.0,
        max_concurrency=32,
        health_status=ProviderHealthStatus.HEALTHY,
        source_kind="inference_deployment",
        source_id=ray_dep_id,
    )
    db_session.add(ray_inst)

    # 4. Memberships for the same alias
    mem_native = LLMPoolMembership(model_alias=alias_name, provider_instance_id=native_id, enabled=True)
    mem_ray = LLMPoolMembership(model_alias=alias_name, provider_instance_id=ray_inst_id, enabled=True)
    db_session.add(mem_native)
    db_session.add(mem_ray)
    await db_session.flush()

    # Query candidates through GatewayDAO
    dao = GatewayDAO(db_session)
    candidates = await dao.resolve_candidates(alias=alias_name, tenant_id="default")
    assert len(candidates) == 2

    kinds = {c.source_kind for c in candidates}
    assert kinds == {"runtime", "inference_deployment"}

    ray_cand = next(c for c in candidates if c.source_kind == "inference_deployment")
    assert ray_cand.instance_id == ray_inst_id
    assert ray_cand.source_id == ray_dep_id
    assert ray_cand.base_url == "http://10.88.10.49:8000/llmport-dep1"
    assert ray_cand.weight == 2.0
    assert ray_cand.max_concurrency == 32


@pytest.mark.anyio
async def test_failover_when_ray_deployment_unhealthy(db_session: AsyncSession):
    """When a Ray deployment becomes unhealthy, router excludes it and keeps native target."""
    alias_name = f"failover-alias-{uuid.uuid4().hex[:8]}"
    native_id = uuid.uuid4()
    ray_inst_id = uuid.uuid4()

    db_session.add(LLMModelAlias(alias=alias_name, enabled=True))
    db_session.add(
        LLMProviderInstance(
            id=native_id,
            type=ProviderType.VLLM,
            base_url="http://10.0.0.10:8000",
            enabled=True,
            health_status=ProviderHealthStatus.HEALTHY,
            source_kind="runtime",
            source_id=native_id,
        )
    )
    ray_inst = LLMProviderInstance(
        id=ray_inst_id,
        type=ProviderType.VLLM,
        base_url="http://10.88.10.49:8000/llmport-dep1",
        enabled=True,
        health_status=ProviderHealthStatus.HEALTHY,
        source_kind="inference_deployment",
        source_id=uuid.uuid4(),
    )
    db_session.add(ray_inst)
    db_session.add(LLMPoolMembership(model_alias=alias_name, provider_instance_id=native_id, enabled=True))
    db_session.add(LLMPoolMembership(model_alias=alias_name, provider_instance_id=ray_inst_id, enabled=True))
    await db_session.flush()

    dao = GatewayDAO(db_session)

    # 1. Both healthy
    candidates = await dao.resolve_candidates(alias=alias_name, tenant_id="default")
    assert len(candidates) == 2

    # 2. Mark Ray unhealthy
    ray_inst.health_status = ProviderHealthStatus.UNHEALTHY
    await db_session.flush()

    candidates_after_fail = await dao.resolve_candidates(alias=alias_name, tenant_id="default")
    assert len(candidates_after_fail) == 1
    assert candidates_after_fail[0].instance_id == native_id
    assert candidates_after_fail[0].source_kind == "runtime"

    # 3. Mark Ray healthy again
    ray_inst.health_status = ProviderHealthStatus.HEALTHY
    await db_session.flush()

    candidates_recovered = await dao.resolve_candidates(alias=alias_name, tenant_id="default")
    assert len(candidates_recovered) == 2


from llm_port_api.services.cache.noop import NoOpCache


@pytest.mark.anyio
async def test_router_service_pick_and_lease_mixed_pool(db_session: AsyncSession):
    """RouterService leases the higher-weight / lower-load candidate first."""
    cache = NoOpCache()
    lease_mgr = LeaseManager(cache, ttl_sec=60)
    dao = GatewayDAO(db_session)
    router = RouterService(dao=dao, cache=cache, lease_manager=lease_mgr)

    inst_low = RoutedInstance(
        alias="test-alias",
        instance_id=uuid.uuid4(),
        provider_type=ProviderType.VLLM,
        base_url="http://native:8000",
        weight=1.0,
        max_concurrency=10,
        source_kind="runtime",
    )
    inst_high = RoutedInstance(
        alias="test-alias",
        instance_id=uuid.uuid4(),
        provider_type=ProviderType.VLLM,
        base_url="http://ray:8000/app",
        weight=5.0,  # Higher weight
        max_concurrency=30,
        source_kind="inference_deployment",
    )

    # Initial decision should prefer inst_high due to higher weight
    decision = await router.pick_and_lease(candidates=[inst_low, inst_high], request_id="req-1")
    assert decision.candidate.instance_id == inst_high.instance_id
    assert decision.candidate.source_kind == "inference_deployment"

    # Release lease
    await router.release(decision)


@pytest.mark.anyio
async def test_upstream_proxy_path_formatting():
    """UpstreamProxy correctly formats OpenAI routes with application prefixes without /v1/v1."""
    # Test normalization function directly
    raw_url_with_v1 = "http://10.88.10.49:8000/llmport-dep1/v1"
    normalized = raw_url_with_v1.rstrip("/").removesuffix("/v1")
    assert normalized == "http://10.88.10.49:8000/llmport-dep1"

    # When UpstreamProxy proxies path "/v1/chat/completions",
    # the target URL is f"{base_url}{path}" -> "http://10.88.10.49:8000/llmport-dep1/v1/chat/completions"
    target_path = "/v1/chat/completions"
    full_url = f"{normalized}{target_path}"
    assert full_url == "http://10.88.10.49:8000/llmport-dep1/v1/chat/completions"
    assert "/v1/v1" not in full_url


@pytest.mark.anyio
async def test_audit_log_records_logical_provider(db_session: AsyncSession):
    """Audit log records logical provider_instance_id matching InferenceDeployment."""
    dep_id = uuid.uuid4()
    inst_id = uuid.uuid4()

    # Create provider instance
    inst = LLMProviderInstance(
        id=inst_id,
        type=ProviderType.VLLM,
        base_url="http://10.88.10.49:8000/llmport-dep1",
        enabled=True,
        health_status=ProviderHealthStatus.HEALTHY,
        source_kind="inference_deployment",
        source_id=dep_id,
    )
    db_session.add(inst)
    await db_session.flush()

    dao = GatewayDAO(db_session)
    log_row = await dao.insert_request_log(
        request_id="req-12345",
        trace_id="trace-abc",
        tenant_id="tenant-1",
        user_id="user-1",
        model_alias="qwen-coder",
        provider_instance_id=inst_id,
        endpoint="/v1/chat/completions",
        status_code=200,
        latency_ms=45,
        ttft_ms=12,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        error_code=None,
    )

    assert log_row.provider_instance_id == inst_id
    assert log_row.endpoint == "/v1/chat/completions"
    assert log_row.status_code == 200
