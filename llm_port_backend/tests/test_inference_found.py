"""Phase 8.2: routing a vLLM container LLM.Port found, as it is.

The containers come from the agent's inventory (``vllm_containers``); the
gateway and the container's own ``/v1/models`` answer are faked. Nothing here
may touch the container itself -- routing it is the whole of it.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import InferenceAdoption
from llm_port_backend.db.models.llm import LLMProvider
from llm_port_backend.db.models.node_control import InfraNode, InfraNodeInventorySnapshot
from llm_port_backend.services.inference import found as found_vllm


def _embed(**over: Any) -> dict[str, Any]:
    """The workstation's hand-started embedding server, as the agent reports it."""
    container = {
        "name": "Qwen3-Embed",
        "image": "vllm/vllm-openai:v0.8.5",
        "state": "running",
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "served_model_names": ["qwen3-embedding"],
        "port": 8000,
        "host_port": 7997,
        "task": "embeddings",
        "task_from": "flags",
        "api_key_required": False,
        "managed_by": None,
    }
    container.update(over)
    return container


class _Gateway:
    enabled = True

    def __init__(self, taken: dict[str, bool] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.taken = taken or {}

    async def members(self, alias: str) -> list[dict[str, Any]]:
        return [{"member_enabled": True}] if self.taken.get(alias) else []

    async def publish_runtime(self, **kw: Any) -> None:
        self.calls.append(("publish", kw))

    async def unpublish_runtime(self, **kw: Any) -> None:
        self.calls.append(("unpublish", kw))

    async def set_instance_enabled(self, **kw: Any) -> None:
        self.calls.append(("enabled", kw))

    async def set_instance_health(self, **kw: Any) -> None:
        self.calls.append(("health", kw))


@pytest.fixture()
def answers(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """What each container answers to ``GET /v1/models``, by base URL."""
    table: dict[str, dict[str, Any]] = {}

    async def probe(url: str) -> dict[str, Any]:
        return table.get(url, {"ok": False, "models": [], "needs_key": False, "error": f"No answer from {url}"})

    monkeypatch.setattr(found_vllm, "probe", probe)
    return table


async def _machine(session: AsyncSession, containers: list[dict[str, Any]], host: str = "10.88.10.220") -> InfraNode:
    node = InfraNode(agent_id=f"ws-{uuid.uuid4().hex[:6]}", host=host, status="healthy", capabilities_json={})
    session.add(node)
    await session.flush()
    session.add(InfraNodeInventorySnapshot(node_id=node.id, inventory_json={"vllm_containers": containers}))
    await session.flush()
    return node


async def _report(session: AsyncSession, node: InfraNode, containers: list[dict[str, Any]]) -> None:
    """A newer inventory from the machine."""
    from datetime import UTC, datetime, timedelta

    session.add(InfraNodeInventorySnapshot(
        node_id=node.id, inventory_json={"vllm_containers": containers},
        created_at=datetime.now(tz=UTC) + timedelta(seconds=len(containers) + 1),
    ))
    await session.flush()


def _entry(entries: list[dict[str, Any]], node: InfraNode, name: str) -> dict[str, Any]:
    return next(e for e in entries if e["node"]["id"] == str(node.id) and e["container"]["name"] == name)


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


async def test_found_containers_are_listed_with_where_to_reach_them(
    dbsession: AsyncSession, answers: dict,
) -> None:
    node = await _machine(dbsession, [_embed()])
    answers["http://10.88.10.220:7997/v1"] = {"ok": True, "models": ["qwen3-embedding"], "needs_key": False, "error": None}

    entry = _entry(await found_vllm.found(dbsession), node, "Qwen3-Embed")

    assert entry["base_url"] == "http://10.88.10.220:7997/v1"
    assert entry["check"]["models"] == ["qwen3-embedding"]
    assert entry["can_route"] is True and entry["reason"] is None


@pytest.mark.parametrize(
    ("over", "says"),
    [
        ({"state": "exited"}, "not running"),
        ({"host_port": None}, "port is not published"),
        ({"task": "scoring", "name": "Qwen3-Rerank"}, "does not route yet"),
        ({"api_key_required": True}, "asks for an API key"),
    ],
)
async def test_what_cannot_be_routed_says_why(
    dbsession: AsyncSession, answers: dict, over: dict, says: str,
) -> None:
    container = _embed(**over)
    node = await _machine(dbsession, [container])
    entry = _entry(await found_vllm.found(dbsession, check=False), node, container["name"])
    assert entry["can_route"] is False
    assert says in entry["reason"]


async def test_a_container_that_does_not_answer_says_so(dbsession: AsyncSession, answers: dict) -> None:
    node = await _machine(dbsession, [_embed()])
    entry = _entry(await found_vllm.found(dbsession), node, "Qwen3-Embed")
    assert entry["can_route"] is False
    assert entry["reason"].startswith("No answer from http://10.88.10.220:7997/v1")


# ---------------------------------------------------------------------------
# Routing it, and stopping
# ---------------------------------------------------------------------------


async def test_routing_publishes_it_under_the_name_and_touches_nothing_else(
    dbsession: AsyncSession, answers: dict,
) -> None:
    node = await _machine(dbsession, [_embed()])
    answers["http://10.88.10.220:7997/v1"] = {"ok": True, "models": ["qwen3-embedding"], "needs_key": False, "error": None}
    gateway = _Gateway()

    adoption = await found_vllm.route(
        dbsession, gateway, node_id=node.id, container_name="Qwen3-Embed", alias="embeddings",
    )

    assert [c[0] for c in gateway.calls] == ["publish"], "a route, and nothing sent to the machine"
    published = gateway.calls[0][1]
    assert published["alias"] == "embeddings"
    assert published["base_url"] == "http://10.88.10.220:7997/v1"
    assert published["litellm_model"] == "qwen3-embedding", "the name the container answers to"
    assert published["source_kind"] == "found_container" and published["source_id"] == adoption.id
    assert adoption.state == "routed" and adoption.task == "embeddings"

    provider = await dbsession.get(LLMProvider, adoption.provider_id)
    assert provider.source_kind == "found_container" and provider.source_id == str(adoption.id)
    assert provider.endpoint_url == "http://10.88.10.220:7997/v1"
    assert provider.capabilities["found_on"] == node.agent_id

    entry = _entry(await found_vllm.found(dbsession, check=False), node, "Qwen3-Embed")
    assert entry["adoption"]["alias"] == "embeddings" and entry["can_route"] is False

    with pytest.raises(found_vllm.FoundError, match="already routed"):
        await found_vllm.route(dbsession, gateway, node_id=node.id, container_name="Qwen3-Embed", alias="other")


async def test_a_name_that_routes_to_something_else_is_refused(dbsession: AsyncSession, answers: dict) -> None:
    node = await _machine(dbsession, [_embed()])
    answers["http://10.88.10.220:7997/v1"] = {"ok": True, "models": ["qwen3-embedding"], "needs_key": False, "error": None}
    with pytest.raises(found_vllm.FoundError, match="already routes to something else"):
        await found_vllm.route(
            dbsession, _Gateway(taken={"qwen2.5-0.5b-instruct": True}),
            node_id=node.id, container_name="Qwen3-Embed", alias="qwen2.5-0.5b-instruct",
        )
    with pytest.raises(found_vllm.FoundError, match="Give it a name"):
        await found_vllm.route(dbsession, _Gateway(), node_id=node.id, container_name="Qwen3-Embed", alias="has space")


async def test_a_container_that_turns_out_to_want_a_key_is_refused(dbsession: AsyncSession, answers: dict) -> None:
    """The key can be set through its environment, which the agent never reads."""
    node = await _machine(dbsession, [_embed()])
    answers["http://10.88.10.220:7997/v1"] = {"ok": False, "models": [], "needs_key": True, "error": "It asks for an API key."}
    with pytest.raises(found_vllm.FoundError, match="API key"):
        await found_vllm.route(dbsession, _Gateway(), node_id=node.id, container_name="Qwen3-Embed", alias="e")
    rows = await dbsession.execute(select(InferenceAdoption).where(InferenceAdoption.node_id == node.id))
    assert rows.first() is None, "nothing recorded for a refusal"


async def test_releasing_takes_the_route_and_the_provider_away(dbsession: AsyncSession, answers: dict) -> None:
    node = await _machine(dbsession, [_embed()])
    answers["http://10.88.10.220:7997/v1"] = {"ok": True, "models": ["qwen3-embedding"], "needs_key": False, "error": None}
    gateway = _Gateway()
    adoption = await found_vllm.route(dbsession, gateway, node_id=node.id, container_name="Qwen3-Embed", alias="emb")
    provider_id = adoption.provider_id

    await found_vllm.release(dbsession, gateway, adoption)
    await dbsession.flush()

    assert gateway.calls[-1] == ("unpublish", {"runtime_id": adoption.id, "alias": "emb"})
    assert await dbsession.get(LLMProvider, provider_id) is None
    assert adoption.state == "released"
    entry = _entry(await found_vllm.found(dbsession, check=False), node, "Qwen3-Embed")
    assert entry["adoption"] is None and entry["can_route"] is True, "it can be routed again"


# ---------------------------------------------------------------------------
# Following the container
# ---------------------------------------------------------------------------


async def test_a_routed_container_is_routed_only_while_it_runs(dbsession: AsyncSession, answers: dict) -> None:
    node = await _machine(dbsession, [_embed()])
    answers["http://10.88.10.220:7997/v1"] = {"ok": True, "models": ["qwen3-embedding"], "needs_key": False, "error": None}
    gateway = _Gateway()
    adoption = await found_vllm.route(dbsession, gateway, node_id=node.id, container_name="Qwen3-Embed", alias="emb")
    gateway.calls.clear()

    assert await found_vllm.follow_containers(dbsession, gateway) == 0, "running, as routed"

    await _report(dbsession, node, [_embed(state="exited")])
    assert await found_vllm.follow_containers(dbsession, gateway) == 1
    assert ("enabled", {"runtime_id": adoption.id, "enabled": False}) in gateway.calls
    assert await found_vllm.follow_containers(dbsession, gateway) == 0, "no churn while it stays stopped"

    await _report(dbsession, node, [_embed(state="running"), _embed(name="x")])
    assert await found_vllm.follow_containers(dbsession, gateway) == 1
    assert ("enabled", {"runtime_id": adoption.id, "enabled": True}) in gateway.calls


async def test_a_container_that_was_removed_stops_being_routed(dbsession: AsyncSession, answers: dict) -> None:
    node = await _machine(dbsession, [_embed()])
    answers["http://10.88.10.220:7997/v1"] = {"ok": True, "models": ["qwen3-embedding"], "needs_key": False, "error": None}
    gateway = _Gateway()
    adoption = await found_vllm.route(dbsession, gateway, node_id=node.id, container_name="Qwen3-Embed", alias="emb")

    await _report(dbsession, node, [])
    await found_vllm.follow_containers(dbsession, gateway)

    assert adoption.detail_json["container_state"] == "gone"
    assert ("enabled", {"runtime_id": adoption.id, "enabled": False}) in gateway.calls


async def test_the_gateway_record_names_its_source() -> None:
    """publish_runtime carries a found container's own kind and id to the gateway."""
    from llm_port_backend.services.llm.gateway_sync import GatewaySyncService

    executed: list[dict[str, Any]] = []

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_exc: Any) -> None: ...

        async def execute(self, _statement: Any, params: dict | None = None) -> None:
            executed.append(dict(params or {}))

        async def commit(self) -> None: ...

    adoption_id = uuid.uuid4()
    await GatewaySyncService(lambda: _Session()).publish_runtime(
        runtime_id=adoption_id, alias="emb", base_url="http://h:7997/v1", backend_provider_type="vllm",
        is_remote=False, source_kind="found_container", source_id=adoption_id,
    )
    instance = next(p for p in executed if "source_kind" in p)
    assert instance["source_kind"] == "found_container" and instance["source_id"] == adoption_id


# ---------------------------------------------------------------------------
# Every route says what kind of model it is
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("generic", "provider", "kind"),
    [
        ({}, {}, "chat"),
        ({"task": "embed"}, {}, "embeddings"),
        ({}, {"engine_args": {"--task": "score"}}, "scoring"),
        ({}, {"engine_args": {"runner": "pooling"}}, "embeddings"),
        ({}, {"extra_args": "--max-model-len 4096 --task=embed"}, "embeddings"),
        ({"task": "generate"}, {}, "chat"),
    ],
)
def test_a_runtime_declares_its_kind_from_its_flags(generic: dict, provider: dict, kind: str) -> None:
    from types import SimpleNamespace

    from llm_port_backend.services.llm.kinds import runtime_kind

    assert runtime_kind(SimpleNamespace(generic_config=generic, provider_config=provider)) == kind


async def test_a_deployment_declares_itself_a_chat_model() -> None:
    from llm_port_backend.services.llm.gateway_sync import GatewaySyncService

    executed: list[dict[str, Any]] = []

    class _Result:
        def scalar(self) -> None:
            return None

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_exc: Any) -> None: ...

        async def execute(self, _statement: Any, params: dict | None = None) -> _Result:
            executed.append(dict(params or {}))
            return _Result()

        async def commit(self) -> None: ...

    await GatewaySyncService(lambda: _Session()).publish_inference_endpoint(
        deployment_id=uuid.uuid4(), base_url="http://h:8000/app/v1", alias="qwen2.5-0.5b-instruct",
    )
    instance = next(p for p in executed if "node_metadata" in p)
    assert instance["node_metadata"] == '{"task": "chat"}'


async def test_a_found_container_declares_the_kind_it_was_found_as(dbsession: AsyncSession, answers: dict) -> None:
    node = await _machine(dbsession, [_embed()])
    answers["http://10.88.10.220:7997/v1"] = {"ok": True, "models": ["qwen3-embedding"], "needs_key": False, "error": None}
    gateway = _Gateway()
    await found_vllm.route(dbsession, gateway, node_id=node.id, container_name="Qwen3-Embed", alias="emb")
    assert gateway.calls[0][1]["task"] == "embeddings"
