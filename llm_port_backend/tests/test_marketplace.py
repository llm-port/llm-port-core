"""The model marketplace: facts from the Hub, fit on a cluster, suggested settings, hosting.

The hardware in these tests is the DGX Spark pair as its agents report it
(two GB10s, 124 610 MiB each, one holding a model), and the models are real
Hub entries with their real sizes, so the verdicts are the ones an operator
on that pair would see.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.services.marketplace import facts, fit, hub, recipes, settings
from llm_port_backend.services.marketplace.hardware import gpus_from_utilization
from llm_port_backend.services.marketplace.host import HostError, HostRequest, build_spec

GB10_MIB = 124_610
GIB = 1024**3

QWEN3_8B_CONFIG = {
    "architectures": ["Qwen3ForCausalLM"], "model_type": "qwen3", "hidden_size": 4096, "num_attention_heads": 32,
    "num_key_value_heads": 8, "head_dim": 128, "num_hidden_layers": 36, "max_position_embeddings": 40960,
    "torch_dtype": "bfloat16",
}


def dgx_pair(*, busy_first: bool = True) -> fit.ClusterHardware:
    utilization = [
        {"gpu": {"count": 1, "devices": [{"name": "NVIDIA GB10", "memory_total_mib": GB10_MIB,
                                          "memory_used_mib": 104_000 if busy_first else 7_000}]}},
        {"gpu": {"count": 1, "devices": [{"name": "NVIDIA GB10", "memory_total_mib": GB10_MIB,
                                          "memory_used_mib": 6_949}]}},
    ]
    return fit.ClusterHardware(
        environment_id="env-dgx", name="dgx-pair", status="ready",
        machines=[fit.Machine(node_id=f"n{i}", name=f"spark-{i}", gpus=gpus_from_utilization(u))
                  for i, u in enumerate(utilization)],
        vllm_version="0.27.1",
    )


def one_gpu(mib: int = 24_576) -> fit.ClusterHardware:
    return fit.ClusterHardware(
        environment_id="env-ws", name="workstation", status="ready",
        machines=[fit.Machine(node_id="w", name="ws", gpus=[fit.Gpu("TITAN RTX", mib * 1024**2, mib * 1024**2)])],
    )


# ── facts ────────────────────────────────────────────────────────────────


def test_weights_are_sized_from_the_dtype_breakdown_and_unknown_stays_unknown() -> None:
    assert facts.weights_bytes({"BF16": 8_190_735_360}) == 16_381_470_720
    assert facts.weights_bytes({"BF16": 1000, "F32": 10}) == 2040
    assert facts.weights_bytes({}) is None
    assert facts.weights_bytes({"Q4_K": 5}) is None, "a dtype we cannot size is not a guess"


def test_quantization_is_read_from_config_first_then_the_name() -> None:
    assert facts.detect_quantization("x/y", [], {"quantization_config": {"quant_method": "awq"}}, {}) == "awq"
    assert facts.detect_quantization("RedHatAI/Llama-3.1-8B-Instruct-FP8", [], {}, {}) == "fp8"
    assert facts.detect_quantization("nvidia/Qwen3-8B-NVFP4", [], {}, {}) == "nvfp4"
    assert facts.detect_quantization("Qwen/Qwen3-8B", ["safetensors"], {}, {"BF16": 1}) is None


def test_capabilities() -> None:
    caps = facts.detect_capabilities(repo_id="Qwen/Qwen3-8B", pipeline_tag="text-generation", tags=[],
                                     architectures=["Qwen3ForCausalLM"], chat_template="{% if tools %}...")
    assert caps == ["tools", "reasoning"]
    caps = facts.detect_capabilities(repo_id="Qwen/Qwen3-Embedding-0.6B", pipeline_tag="feature-extraction",
                                     tags=["sentence-transformers"], architectures=[], chat_template="tools")
    assert "embedding" in caps and "tools" not in caps and "reasoning" not in caps
    caps = facts.detect_capabilities(repo_id="Qwen/Qwen3-VL-8B-Instruct", pipeline_tag="image-text-to-text",
                                     tags=[], architectures=["Qwen3VLForConditionalGeneration"], chat_template=None)
    assert "vision" in caps


def test_architecture_from_config_sizes_the_kv_cache() -> None:
    arch = facts.architecture_from_config(QWEN3_8B_CONFIG)
    assert (arch.num_layers, arch.num_kv_heads, arch.head_dim, arch.max_context) == (36, 8, 128, 40960)
    assert arch.kv_bytes_per_token() == 2 * 36 * 8 * 128 * 2  # 147 456 bytes per token
    vlm = facts.architecture_from_config({"model_type": "gemma3", "text_config": {
        "num_hidden_layers": 62, "num_attention_heads": 32, "num_key_value_heads": 16, "head_dim": 128}})
    assert vlm.num_layers == 62 and vlm.kv_bytes_per_token() is not None
    assert facts.architecture_from_config({"auto_map": {"AutoModel": "x"}}).needs_remote_code


def test_a_hub_entry_becomes_a_card() -> None:
    card = hub.card_from_dict({
        "id": "Qwen/Qwen3-8B", "downloads": 12_522_822, "likes": 2039, "pipeline_tag": "text-generation",
        "tags": ["safetensors", "qwen3", "license:apache-2.0"], "gated": False,
        "safetensors_parameters": {"BF16": 8_190_735_360},
        "config": {"architectures": ["Qwen3ForCausalLM"], "model_type": "qwen3",
                   "tokenizer_config": {"chat_template": "{%- if tools %}<tools>{% endif %}"}},
        "card": {"license": "apache-2.0"}, "siblings": [],
    })
    assert card["params_b"] == 8.19 and card["weights_bytes"] == 16_381_470_720
    assert card["runnable"] and card["format"] == "safetensors" and card["task"] == "chat"
    assert set(card["capabilities"]) == {"tools", "reasoning"}
    gguf = hub.card_from_dict({"id": "unsloth/Qwen3-8B-GGUF", "tags": ["gguf"], "pipeline_tag": "text-generation"})
    assert gguf["runnable"] is False and gguf["not_runnable_reason"] == "gguf"
    assert gguf["weights_bytes"] is None, "no size is unknown, never zero"


def test_the_readme_summary_is_the_first_real_paragraph() -> None:
    text = "---\nlicense: mit\n---\n# Title\n\n[![badge](x)](y)\n\nShort.\n\n" \
           "Qwen3 is the latest generation of large language models in the Qwen series, offering dense and MoE models.\n"
    assert hub.readme_summary(text).startswith("Qwen3 is the latest generation")


# ── fit ──────────────────────────────────────────────────────────────────


def qwen3_8b_needs() -> fit.ModelNeeds:
    arch = facts.architecture_from_config(QWEN3_8B_CONFIG)
    return fit.ModelNeeds(weights_bytes=16_381_470_720, kv_bytes_per_token=arch.kv_bytes_per_token(),
                          max_context=40960, attention_heads=32)


def test_an_8b_model_fits_one_gb10_and_says_so_with_the_numbers() -> None:
    plan = fit.plan(qwen3_8b_needs(), dgx_pair())
    assert plan.status == "fits" and plan.tensor_parallel == 1
    assert plan.gpus_per_copy == plan.suggested_gpu_memory_utilization < 1, "small enough to share a GB10"
    assert plan.copies >= 2 and plan.copies_now >= 1
    assert plan.max_context == 40960, "the whole trained context fits"


def test_a_model_bigger_than_one_card_spans_two() -> None:
    llama70 = fit.ModelNeeds(weights_bytes=141_000_000_000, kv_bytes_per_token=2 * 80 * 8 * 128 * 2,
                             max_context=131072, attention_heads=64)
    plan = fit.plan(llama70, dgx_pair(busy_first=False))
    assert plan.status == "fits" and plan.tensor_parallel == 2 and plan.gpus_per_copy == 2
    assert plan.copies == 1
    busy = fit.plan(llama70, dgx_pair(busy_first=True))
    assert busy.status == "fits" and busy.copies_now == 0 and "busy_now" in busy.notes, \
        "fits the pair, but not while the other model holds a card"


def test_too_large_and_unknown_are_said_plainly() -> None:
    huge = fit.ModelNeeds(weights_bytes=400 * 10**9, kv_bytes_per_token=100_000, max_context=None)
    plan = fit.plan(huge, one_gpu())
    assert plan.status == "too_large" and plan.needed_bytes_per_gpu > plan.gpu_bytes
    assert fit.plan(fit.ModelNeeds(None, None, None), one_gpu()).status == "unknown"
    empty = fit.ClusterHardware("e", "empty", "ready", machines=[fit.Machine("n", "n", [])])
    assert fit.plan(qwen3_8b_needs(), empty).status == "no_accelerators"


def test_tensor_parallel_respects_attention_heads() -> None:
    odd = fit.ModelNeeds(weights_bytes=40 * 10**9, kv_bytes_per_token=10_000, max_context=8192, attention_heads=3)
    four = fit.ClusterHardware("e", "4", "ready", machines=[
        fit.Machine("n", "n", [fit.Gpu("A", 24 * GIB, 24 * GIB) for _ in range(4)])])
    assert fit.plan(odd, four).status == "too_large", "3 heads split over 2 or 4 cards is not possible"


# ── suggested settings ──────────────────────────────────────────────────


def test_suggested_parsers_are_ones_the_runtime_registers() -> None:
    cases = {
        "Qwen/Qwen3-8B": ("hermes", "qwen3"),
        "Qwen/Qwen3-4B-Instruct-2507": ("hermes", None),
        "Qwen/Qwen3-Coder-30B-A3B-Instruct": ("qwen3_coder", None),
        "meta-llama/Llama-3.1-8B-Instruct": ("llama3_json", None),
        "openai/gpt-oss-20b": ("openai", "openai_gptoss"),
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B": ("hermes", "deepseek_r1"),
        "microsoft/Phi-4-mini-instruct": ("phi4_mini_json", None),
    }
    for repo, (tool, reasoning) in cases.items():
        assert settings.tool_parser_for(repo) == tool, repo
        assert settings.reasoning_parser_for(repo) == reasoning, repo
    for pattern, parser in settings._TOOL_RULES:  # noqa: SLF001
        assert parser in settings.TOOL_PARSERS, pattern
    for pattern, parser in settings._REASONING_RULES:  # noqa: SLF001
        assert not parser or parser in settings.REASONING_PARSERS, pattern


def test_suggestions_for_a_chat_and_an_embedding_model() -> None:
    plan = fit.plan(qwen3_8b_needs(), dgx_pair())
    chat = settings.suggest({"repo_id": "Qwen/Qwen3-8B", "capabilities": ["tools", "reasoning"],
                             "max_context": 40960}, plan)
    assert chat["config"]["tool_call_parser"] == "hermes" and chat["config"]["enable_auto_tool_choice"]
    assert chat["config"]["reasoning_parser"] == "qwen3"
    assert chat["config"]["max_model_len"] == 32768, "capped by default below the trained 40960"
    assert chat["config"]["gpu_memory_utilization"] == plan.suggested_gpu_memory_utilization
    embed = settings.suggest({"repo_id": "Qwen/Qwen3-Embedding-0.6B", "capabilities": ["embedding"],
                              "architecture": "Qwen3ForCausalLM"}, None)
    assert embed["config"] == {"runner": "pooling", "convert": "embed"}


# ── the spec hosting writes ─────────────────────────────────────────────


def _request(**over: Any) -> HostRequest:
    base = dict(repo_id="Qwen/Qwen3-8B", environment_id=uuid.uuid4(), name="qwen3-8b", alias="qwen3-8b",
                copies=1, gpus_per_copy=1, engine_config={"max_model_len": 32768})
    base.update(over)
    return HostRequest(**base)


@pytest.mark.parametrize("gpus, extra", [(1, {}), (2, {"topology": {"tensor_parallel_size": 2}}), (0.3, {})])
def test_host_specs_compile_for_ray(gpus: float, extra: dict[str, Any]) -> None:
    from llm_port_backend.services.inference.drivers.ray.compiler import compile_deployment

    spec = build_spec(_request(gpus_per_copy=gpus, engine_config={"gpu_memory_utilization": 0.5,
                                                                   "tensor_parallel_size": 8}))
    for key, value in extra.items():
        assert spec[key] == value
    assert "tensor_parallel_size" not in spec["engine"]["config"], "the copy's shape comes from the form"
    if gpus < 1:
        assert spec["engine"]["config"]["gpu_memory_utilization"] == 0.3, "a shared card is not over-claimed"
    compiled = compile_deployment(spec_data=spec, model_display_name="Qwen3-8B", model_source="huggingface",
                                  hf_repo_id="Qwen/Qwen3-8B")
    assert compiled["llm_configs"][0]["engine_kwargs"]["gpu_memory_utilization"] == min(0.5, gpus)


def test_a_shared_card_is_not_over_claimed_by_vllms_default() -> None:
    spec = build_spec(_request(gpus_per_copy=0.25, engine_config={}))
    assert spec["engine"]["config"]["gpu_memory_utilization"] == 0.25
    whole = build_spec(_request(gpus_per_copy=1, engine_config={}))
    assert "gpu_memory_utilization" not in whole["engine"]["config"]


def test_host_refuses_impossible_shapes() -> None:
    with pytest.raises(HostError):
        build_spec(_request(gpus_per_copy=1.5))
    with pytest.raises(HostError):
        build_spec(_request(gpus_per_copy=0))


# ── the API, with the Hub replaced ──────────────────────────────────────


@pytest.fixture()
def fake_hub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    hub.clear_cache()
    state: dict[str, Any] = {"online": True, "downloads": []}
    card = hub.card_from_dict({
        "id": "Qwen/Qwen3-8B", "pipeline_tag": "text-generation", "tags": [], "downloads": 5,
        "safetensors_parameters": {"BF16": 8_190_735_360},
        "config": {"architectures": ["Qwen3ForCausalLM"], "tokenizer_config": {"chat_template": "tools"}},
    })

    async def search(self: Any, query: str = "", **_: Any) -> list[dict[str, Any]]:
        if not state["online"]:
            raise hub.HubUnavailable("no route to host")
        return [dict(card)]

    async def cards_for(self: Any, repos: list[str]) -> dict[str, dict[str, Any]]:
        if not state["online"]:
            raise hub.HubUnavailable("no route to host")
        return {"Qwen/Qwen3-8B": dict(card)} if "Qwen/Qwen3-8B" in repos else {}

    async def detail(self: Any, repo_id: str) -> dict[str, Any]:
        if not state["online"]:
            raise hub.HubUnavailable("no route to host")
        arch = facts.architecture_from_config(QWEN3_8B_CONFIG)
        return {**card, "architecture_facts": arch.to_dict(), "max_context": 40960,
                "kv_bytes_per_token": arch.kv_bytes_per_token(), "needs_remote_code": False, "files": []}

    async def recipe_for(repo_id: str, *, accelerator: str | None = None, **_: Any) -> Any:
        data = state.get("recipe")
        return recipes.parse_recipe(data, accelerator=accelerator) if data else None

    monkeypatch.setattr(hub.HubClient, "search", search)
    monkeypatch.setattr(hub.HubClient, "cards_for", cards_for)
    monkeypatch.setattr(hub.HubClient, "detail", detail)
    monkeypatch.setattr(recipes, "recipe_for", recipe_for)
    return state


@pytest.fixture()
def authed(fastapi_app: FastAPI) -> FastAPI:
    from unittest.mock import MagicMock

    from llm_port_backend.db.models.users import User, current_active_user

    user = MagicMock(spec=User)
    user.id, user.is_active, user.is_superuser, user.is_verified = uuid.uuid4(), True, True, True
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    return fastapi_app


async def _dgx_cluster(dbsession: AsyncSession) -> str:
    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
    from llm_port_backend.db.models.inference import (
        InferenceControlPlane, InferenceEnvironment, InferenceEnvironmentNode,
    )
    from llm_port_backend.db.models.node_control import InfraNode

    plane = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray", status="pending", config_json={})
    dbsession.add(plane)
    await dbsession.flush()
    env = InferenceEnvironment(control_plane_id=plane.id, name=f"dgx-{uuid.uuid4().hex[:6]}", status="ready",
                               desired_state="running", config_json={}, capabilities_json={}, observed_status_json={})
    dbsession.add(env)
    await dbsession.flush()
    for used in (104_000, 6_949):
        node = InfraNode(agent_id=f"spark-{uuid.uuid4().hex[:6]}", host="10.0.0.1", status="healthy",
                         capabilities_json={"machine": "aarch64", "gpu_vendor": "nvidia", "gpu_count": 1})
        dbsession.add(node)
        await dbsession.flush()
        dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="worker"))
        await NodeControlDAO(dbsession).upsert_inventory_snapshot(
            node_id=node.id, inventory_json={},
            utilization_json={"gpu": {"devices": [{"name": "NVIDIA GB10", "memory_total_mib": GB10_MIB,
                                                   "memory_used_mib": used}]}},
        )
    await dbsession.commit()
    return str(env.id)


@pytest.mark.anyio
async def test_search_and_detail_answer_with_fit(authed: FastAPI, client: AsyncClient, dbsession: AsyncSession,
                                                 fake_hub: dict[str, Any]) -> None:
    env_id = await _dgx_cluster(dbsession)
    r = await client.get("/api/llm/marketplace/search", params={"q": "qwen", "cluster_id": env_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hub"] == "online" and body["items"][0]["fit"]["status"] == "fits"

    r = await client.get("/api/llm/marketplace/models/Qwen/Qwen3-8B", params={"cluster_id": env_id})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["fits"][env_id]["tensor_parallel"] == 1
    assert detail["suggested"]["config"]["tool_call_parser"] == "hermes"
    assert detail["local"] is None


@pytest.mark.anyio
async def test_offline_is_an_answer_not_an_error(authed: FastAPI, client: AsyncClient, dbsession: AsyncSession,
                                                 fake_hub: dict[str, Any]) -> None:
    fake_hub["online"] = False
    r = await client.get("/api/llm/marketplace/search", params={"q": "qwen"})
    assert r.status_code == 200 and r.json() == {**r.json(), "hub": "offline", "items": []}
    r = await client.get("/api/llm/marketplace/recommended")
    assert r.status_code == 200
    body = r.json()
    assert body["hub"] == "offline" and len(body["items"]) >= 10, "the curated list still shows"


@pytest.mark.anyio
async def test_hosting_keeps_the_model_and_creates_the_deployment(
    authed: FastAPI, client: AsyncClient, dbsession: AsyncSession, fake_hub: dict[str, Any],
) -> None:
    from llm_port_backend.db.models.inference import InferenceDeployment
    from llm_port_backend.db.models.llm import LLMModel
    from llm_port_backend.web.api.llm.dependencies import get_llm_service

    env_id = await _dgx_cluster(dbsession)
    started: list[str] = []

    class _Llm:
        async def start_download(self, model_dao: Any, job_dao: Any, *, hf_repo_id: str, **kw: Any) -> Any:
            from llm_port_backend.db.models.llm import ModelSource, ModelStatus

            started.append(hf_repo_id)
            model = await model_dao.create(display_name=kw.get("display_name") or hf_repo_id,
                                           source=ModelSource.HUGGINGFACE, hf_repo_id=hf_repo_id,
                                           hf_revision=None, tags=kw.get("tags"), status=ModelStatus.DOWNLOADING)
            return model, await job_dao.create(model.id)

    authed.dependency_overrides[get_llm_service] = lambda: _Llm()
    r = await client.post("/api/llm/marketplace/host", json={
        "repo_id": "Qwen/Qwen3-8B", "environment_id": env_id, "name": "qwen3-8b", "alias": "qwen3-8b",
        "copies": 1, "gpus_per_copy": 0.3,
        "engine_config": {"tool_call_parser": "hermes", "enable_auto_tool_choice": True, "max_model_len": 32768},
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert started == ["Qwen/Qwen3-8B"] and body["download"] == "started"
    dep = (await dbsession.execute(
        select(InferenceDeployment).where(InferenceDeployment.id == uuid.UUID(body["deployment_id"])),
    )).scalar_one()
    assert dep.spec_json["resources"]["replica"]["gpus"] == 0.3
    assert dep.spec_json["engine"]["config"]["tool_call_parser"] == "hermes"
    assert dep.spec_json["service"]["alias"] == "qwen3-8b"
    model = (await dbsession.execute(select(LLMModel).where(LLMModel.id == dep.model_id))).scalar_one()
    assert model.hf_repo_id == "Qwen/Qwen3-8B"

    r = await client.post("/api/llm/marketplace/host", json={
        "repo_id": "Qwen/Qwen3-8B", "environment_id": env_id, "name": "Bad Name", "copies": 1, "gpus_per_copy": 1,
    })
    assert r.status_code == 422


def test_a_modelopt_checkpoint_is_named_by_what_it_holds() -> None:
    """The list answer says "modelopt" (the tool) without the algorithm; the name says NVFP4."""
    config = {"quantization_config": {"quant_method": "modelopt"}}
    assert facts.detect_quantization("nvidia/Qwen3.8-27B-NVFP4", [], config, None) == "nvfp4"
    assert facts.detect_quantization("nvidia/Some-Model", [], config, None) == "modelopt"


# ── vLLM's own recipes ──────────────────────────────────────────────────

GPT_OSS_RECIPE = {
    "hf_id": "openai/gpt-oss-120b",
    "meta": {"title": "GPT-OSS"},
    "model": {"min_vllm_version": "0.10.1", "context_length": 131072, "base_args": []},
    "features": {
        "tool_calling": {"args": ["--tool-call-parser", "openai", "--enable-auto-tool-choice"]},
        "spec_decoding": {"description": "EAGLE3", "args": [
            "--speculative-config", '{"model":"nvidia/gpt-oss-120b-Eagle3-v3","num_speculative_tokens":7}']},
    },
    "opt_in_features": ["spec_decoding"],
    "hardware_overrides": {
        "blackwell": {"extra_args": ["--quantization-config.moe.activation", "mxfp8"]},
        "hopper": {"extra_args": ["--async-scheduling", "--no-enable-prefix-caching",
                                  "--max-num-batched-tokens", "8192"]},
    },
}

DEEPSEEK_RECIPE = {
    "hf_id": "deepseek-ai/DeepSeek-R1",
    "model": {"min_vllm_version": "0.12.0", "base_args": ["--trust-remote-code", "--enable-expert-parallel",
                                                           "--tensor-parallel-size", "8"]},
    "features": {
        "tool_calling": {"args": ["--enable-auto-tool-choice", "--tool-call-parser", "deepseek_v3",
                                  "--chat-template", "examples/tool_chat_template_deepseekr1.jinja"]},
        "reasoning": {"args": ["--reasoning-parser", "deepseek_r1"]},
    },
    "opt_in_features": [],
}


def test_a_recipe_becomes_settings_this_server_can_use() -> None:
    recipe = recipes.parse_recipe(DEEPSEEK_RECIPE)
    assert recipe.config == {
        "trust_remote_code": True, "enable_expert_parallel": True, "enable_auto_tool_choice": True,
        "tool_call_parser": "deepseek_v3", "reasoning_parser": "deepseek_r1",
    }
    # A path into vLLM's source tree is not in the runtime image; the copy's shape is the fit's.
    assert recipe.dropped == ["--chat-template examples/tool_chat_template_deepseekr1.jinja"]
    assert recipe.url == "https://recipes.vllm.ai/deepseek-ai/DeepSeek-R1"


def test_hardware_overrides_apply_only_to_the_generation_they_were_written_for() -> None:
    assert recipes.hardware_generation("NVIDIA H200") == "hopper"
    assert recipes.hardware_generation("NVIDIA B200") == "blackwell"
    assert recipes.hardware_generation("NVIDIA GB10") is None, "a DGX Spark is not a B200"
    assert recipes.hardware_generation("AMD Instinct MI300X") == "amd"

    on_h200 = recipes.parse_recipe(GPT_OSS_RECIPE, accelerator="NVIDIA H200")
    assert on_h200.hardware == "hopper"
    assert on_h200.config["enable_prefix_caching"] is False
    assert on_h200.config["max_num_batched_tokens"] == 8192
    on_gb10 = recipes.parse_recipe(GPT_OSS_RECIPE, accelerator="NVIDIA GB10")
    assert on_gb10.hardware is None
    assert on_gb10.config == {"tool_call_parser": "openai", "enable_auto_tool_choice": True}
    # An opt-in needing a JSON argument is offered as it is written, not turned into a setting.
    [spec] = on_gb10.opt_in
    assert spec.name == "spec_decoding" and not spec.usable and spec.config == {}


def test_a_recipe_for_a_newer_vllm_says_so() -> None:
    recipe = recipes.parse_recipe(DEEPSEEK_RECIPE)
    assert recipe.to_dict(vllm_version="0.27.1+93523f72.dev")["runtime_too_old"] is False
    assert recipe.to_dict(vllm_version="0.11.2")["runtime_too_old"] is True


@pytest.mark.anyio
async def test_the_recipe_leads_the_suggestions(authed: FastAPI, client: AsyncClient, dbsession: AsyncSession,
                                                fake_hub: dict[str, Any]) -> None:
    env_id = await _dgx_cluster(dbsession)
    fake_hub["recipe"] = {
        "hf_id": "Qwen/Qwen3-8B",
        "model": {"min_vllm_version": "0.8.5", "context_length": 40960, "base_args": []},
        "features": {"tool_calling": {"args": ["--enable-auto-tool-choice", "--tool-call-parser", "qwen3_xml"]},
                     "reasoning": {"args": ["--reasoning-parser", "qwen3"]}},
        "opt_in_features": [],
    }
    r = await client.get("/api/llm/marketplace/models/Qwen/Qwen3-8B", params={"cluster_id": env_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested"]["config"]["tool_call_parser"] == "qwen3_xml"
    assert body["suggested"]["reasons"]["tool_call_parser"] == "recipe"
    assert body["recipe"]["url"] == "https://recipes.vllm.ai/Qwen/Qwen3-8B"
    assert body["recipe"]["runtime_too_old"] is False


@pytest.mark.anyio
async def test_recipes_offline_are_no_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    recipes.clear_cache()

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        assert await recipes.recipe_for("Qwen/Qwen3-8B", client=client) is None
    recipes.clear_cache()

