"""Several kinds of model behind one gateway.

Found running a chat model on a Ray cluster beside an embedding model in a
vLLM container LLM.Port found (2026-09-23):

* embeddings went through the chat path (``test_gateway_api``);
* an embeddings request to the chat model was a 502 rather than a 400;
* capacity slots leaked: a request that outlived its lease never gave its slot
  back, and a burst was refused while nothing was running;
* a burst was refused at once instead of waiting a moment for a slot;
* the model list did not say which model was for what.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import litellm
import pytest
from redis.asyncio import ConnectionPool

from llm_port_api.db.models.gateway import ProviderType
from llm_port_api.services.cache.redis import RedisCache
from llm_port_api.services.gateway.errors import GatewayError
from llm_port_api.services.gateway.lease import LeaseManager
from llm_port_api.services.gateway.llm_adapter import LLMAdapter
from llm_port_api.services.gateway.routing import RouterService


@pytest.mark.anyio
async def test_a_request_that_outlives_its_lease_does_not_keep_its_slot(fake_redis_pool: ConnectionPool) -> None:
    lease = LeaseManager(RedisCache(fake_redis_pool), ttl_sec=1)
    instance = uuid.uuid4()

    assert await lease.try_acquire(instance_id=instance, request_id="hung", max_concurrency=1)
    assert not await lease.try_acquire(instance_id=instance, request_id="next", max_concurrency=1)

    await asyncio.sleep(1.2)  # the hung request's lease runs out; it never releases
    assert await lease.in_flight(instance) == 0
    assert await lease.try_acquire(instance_id=instance, request_id="next", max_concurrency=1)

    # Its release, arriving late, must not free the slot someone else now holds.
    await lease.release(instance_id=instance, request_id="hung")
    assert await lease.in_flight(instance) == 1


def _candidate(max_concurrency: int = 1) -> Any:
    from llm_port_api.db.dao.gateway_dao import RoutedInstance

    return RoutedInstance(
        alias="qwen2.5-0.5b-instruct",
        instance_id=uuid.uuid4(),
        provider_type=ProviderType.VLLM,
        base_url="http://10.88.10.71:8000/llmport-x",
        weight=1.0,
        max_concurrency=max_concurrency,
        source_kind="inference_deployment",
    )


@pytest.mark.anyio
async def test_a_burst_waits_for_a_slot_instead_of_being_refused(fake_redis_pool: ConnectionPool) -> None:
    cache = RedisCache(fake_redis_pool)
    lease = LeaseManager(cache, ttl_sec=30)
    router = RouterService(dao=None, cache=cache, lease_manager=lease, capacity_wait_sec=5)  # type: ignore[arg-type]
    candidate = _candidate()
    first = await router.pick_and_lease(candidates=[candidate], request_id="a")

    async def finish_soon() -> None:
        await asyncio.sleep(0.3)
        await router.release(first)

    started = time.monotonic()
    second, _ = await asyncio.gather(router.pick_and_lease(candidates=[candidate], request_id="b"), finish_soon())
    assert second.request_id == "b"
    assert 0.25 < time.monotonic() - started < 2, "it waited for the slot, and only as long as it had to"


@pytest.mark.anyio
async def test_it_is_refused_only_after_the_wait(fake_redis_pool: ConnectionPool) -> None:
    cache = RedisCache(fake_redis_pool)
    router = RouterService(dao=None, cache=cache, lease_manager=LeaseManager(cache, ttl_sec=30), capacity_wait_sec=0.4)  # type: ignore[arg-type]
    candidate = _candidate()
    await router.pick_and_lease(candidates=[candidate], request_id="a")

    started = time.monotonic()
    with pytest.raises(GatewayError) as refused:
        await router.pick_and_lease(candidates=[candidate], request_id="b")
    assert refused.value.status_code == 503
    assert time.monotonic() - started >= 0.4


@pytest.mark.anyio
async def test_embeddings_asked_of_a_chat_model_are_the_callers_mistake(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(**_kwargs: Any) -> Any:
        raise litellm.exceptions.BadRequestError(
            message="This model does not support the 'embed' task.", model="m", llm_provider="openai",
        )

    monkeypatch.setattr(litellm, "aembedding", refuse)
    result = await LLMAdapter().embedding(
        provider_type=ProviderType.VLLM, base_url="http://h:8000", api_key_encrypted=None,
        litellm_provider=None, litellm_model="Qwen2.5-0.5B-Instruct", extra_params=None,
        payload={"model": "qwen2.5-0.5b-instruct", "input": ["x"]},
    )
    assert result.status_code == 400
    assert "does not support the 'embed' task" in result.payload["error"]["message"]


@pytest.mark.anyio
async def test_the_model_list_says_what_each_model_is_for(db_session: Any) -> None:
    from llm_port_api.db.dao.gateway_dao import GatewayDAO
    from llm_port_api.db.models.gateway import LLMModelAlias, LLMPoolMembership, LLMProviderInstance

    async def route(alias: str, task: str | None) -> None:
        instance = LLMProviderInstance(
            id=uuid.uuid4(), type=ProviderType.VLLM, base_url=f"http://{alias}:8000", enabled=True,
            node_metadata={"task": task} if task else None,
        )
        db_session.add_all([LLMModelAlias(alias=alias, enabled=True), instance])
        await db_session.flush()
        db_session.add(LLMPoolMembership(model_alias=alias, provider_instance_id=instance.id, enabled=True))
        await db_session.flush()

    await route("chat-model", None)
    await route("embed-model", "embeddings")
    await route("rerank-model", "scoring")

    kinds = await GatewayDAO(db_session).alias_kinds(["chat-model", "embed-model", "rerank-model"])
    assert kinds == {"chat-model": None, "embed-model": "embeddings", "rerank-model": "scoring"}


def _routed(task: str | None) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(node_metadata={"task": task} if task else None)


def test_a_request_of_the_wrong_kind_is_refused_before_it_goes_anywhere() -> None:
    from llm_port_api.services.gateway.service import _check_kind

    with pytest.raises(GatewayError) as chat_to_embed:
        _check_kind("/v1/chat/completions", "qwen3-embedding-0.6b", [_routed("embeddings")])
    assert chat_to_embed.value.status_code == 400
    assert chat_to_embed.value.message == "qwen3-embedding-0.6b is an embeddings model: send it to /v1/embeddings."

    with pytest.raises(GatewayError) as embed_to_chat:
        _check_kind("/v1/embeddings", "qwen2.5-0.5b-instruct", [_routed("chat")])
    assert "is a chat model: send it to /v1/chat/completions" in embed_to_chat.value.message

    with pytest.raises(GatewayError, match="scoring"):
        _check_kind("/v1/chat/completions", "qwen3-reranker", [_routed("scoring")])


def test_a_route_that_does_not_say_is_not_refused() -> None:
    """A remote API's kind cannot be known; the upstream decides."""
    from llm_port_api.services.gateway.service import _check_kind

    _check_kind("/v1/chat/completions", "remote-120b", [_routed(None)])
    _check_kind("/v1/embeddings", "remote-120b", [_routed(None)])
    _check_kind("/v1/chat/completions", "mixed", [_routed("embeddings"), _routed(None)])
