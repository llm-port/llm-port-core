"""Hub search: chat includes the multimodal chat models, authors filter, wildcards work; owner avatars."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from llm_port_backend.services.marketplace import curated, hub, settings

pytestmark = pytest.mark.anyio


def _info(repo: str, pipeline: str | None = "image-text-to-text", downloads: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=repo, author=repo.split("/", 1)[0], downloads=downloads, likes=0, trending_score=downloads,
        created_at=None, last_modified=None, pipeline_tag=pipeline, library_name="transformers",
        tags=["conversational", "safetensors"], gated=False, card_data=None, config={}, safetensors=None,
        siblings=[{"rfilename": "model.safetensors"}],
    )


class _Api:
    """Records what was asked of the Hub and answers from a fixed catalogue."""

    catalogue: ClassVar[list[SimpleNamespace]] = [
        _info("Qwen/Qwen3.8-27B", downloads=90),
        _info("Qwen/Qwen3.8-27B-FP8", downloads=80),
        _info("Qwen/Qwen3.8-Flash-Next-FP8", downloads=70),
        _info("unsloth/Qwen3.8-27B-FP8", downloads=60),
        _info("Qwen/Qwen3-8B", pipeline="text-generation", downloads=50),
    ]
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, token: str | None = None) -> None:
        pass

    def list_models(self, **kw: Any) -> list[SimpleNamespace]:
        type(self).calls.append(kw)
        out = self.catalogue
        if kw.get("search"):
            out = [m for m in out if kw["search"].lower() in m.id.lower()]
        if kw.get("author"):
            out = [m for m in out if m.author.lower() == kw["author"].lower()]
        return out[: kw.get("limit", 40)]


@pytest.fixture(autouse=True)
def fake_api(monkeypatch: pytest.MonkeyPatch) -> type[_Api]:
    hub.clear_cache()
    _Api.calls = []
    monkeypatch.setattr(hub.HubClient, "_api", lambda self: _Api())
    return _Api


async def test_chat_asks_for_conversational_models_not_just_text_generation() -> None:
    """Qwen3.5 onwards and Gemma 4 are filed under image-text-to-text: a pipeline filter hid them."""
    cards = await hub.HubClient().search("qwen3.8", task="chat", sort="downloads")
    assert _Api.calls[0].get("filter") == "conversational"
    assert "pipeline_tag" not in _Api.calls[0]
    assert "Qwen/Qwen3.8-27B-FP8" in [c["repo_id"] for c in cards]


async def test_an_author_narrows_the_search_on_the_hub() -> None:
    cards = await hub.HubClient().search("qwen3.8", task="chat", author="Qwen")
    assert _Api.calls[0]["author"] == "Qwen"
    assert all(c["repo_id"].startswith("Qwen/") for c in cards)


async def test_wildcards_match_the_whole_name() -> None:
    cards = await hub.HubClient().search("qwen3.8*27b*fp8", task="chat", sort="downloads")
    assert [c["repo_id"] for c in cards] == ["Qwen/Qwen3.8-27B-FP8", "unsloth/Qwen3.8-27B-FP8"]
    # The Hub has no wildcards: each literal piece was asked for, most selective first.
    assert [c["search"] for c in _Api.calls] == ["qwen3.8", "27b", "fp8"]
    assert hub.wildcard_parts("qwen?.8") == ("*qwen?.8*", ["qwen", ".8"])
    assert hub.wildcard_parts("plain") is None


async def test_the_search_endpoint_takes_an_author(fastapi_app: FastAPI, client: AsyncClient) -> None:
    from llm_port_backend.db.models.users import User, current_active_user

    user = MagicMock(spec=User)
    user.id, user.is_active, user.is_superuser, user.is_verified = uuid.uuid4(), True, True, True
    fastapi_app.dependency_overrides[current_active_user] = lambda: user

    r = await client.get("/api/llm/marketplace/search", params={"q": "qwen3.8", "author": "Qwen"})
    assert r.status_code == 200, r.text
    assert {i["repo_id"].split("/")[0] for i in r.json()["items"]} == {"Qwen"}
    bad = await client.get("/api/llm/marketplace/search", params={"author": "../etc"})
    assert bad.status_code == 422


async def test_an_owner_avatar_is_fetched_once_and_kept() -> None:
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path == "/api/organizations/Qwen/avatar":
            return httpx.Response(200, json={"avatarUrl": "https://cdn-avatars.huggingface.co/q.webp"})
        if request.url.host == "cdn-avatars.huggingface.co":
            return httpx.Response(200, content=b"RIFF....WEBP", headers={"content-type": "image/webp"})
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        first = await hub.fetch_avatar("Qwen", client=client)
        again = await hub.fetch_avatar("Qwen", client=client)
        none = await hub.fetch_avatar("nobody-at-all", client=client)
    assert first == ("image/webp", b"RIFF....WEBP") == again
    assert none is None
    assert sum("Qwen" in u for u in asked) == 1, "kept after the first fetch"
    assert await hub.fetch_avatar("../etc") is None, "never a path"


async def test_an_avatar_that_could_run_script_or_lives_elsewhere_is_refused() -> None:
    """It is served from our own origin: no SVG, and only from where the Hub keeps pictures."""
    fetched: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.host)
        if request.url.path == "/api/organizations/svg-org/avatar":
            return httpx.Response(200, json={"avatarUrl": "https://cdn-avatars.huggingface.co/x.svg"})
        if request.url.path == "/api/organizations/far-org/avatar":
            return httpx.Response(200, json={"avatarUrl": "https://10.0.0.5/inside.png"})
        if request.url.host == "cdn-avatars.huggingface.co":
            return httpx.Response(200, content=b"<svg onload=alert(1)>", headers={"content-type": "image/svg+xml"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        assert await hub.fetch_avatar("svg-org", client=client) is None
        assert await hub.fetch_avatar("far-org", client=client) is None
    assert "10.0.0.5" not in fetched, "a URL off the Hub is never fetched"


async def test_the_avatar_endpoint_serves_the_picture_safely(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from llm_port_backend.db.models.users import User, current_active_user

    user = MagicMock(spec=User)
    user.id, user.is_active, user.is_superuser, user.is_verified = uuid.uuid4(), True, True, True
    fastapi_app.dependency_overrides[current_active_user] = lambda: user

    async def fake(author: str, **_: Any) -> tuple[str, bytes] | None:
        return ("image/webp", b"RIFF") if author == "Qwen" else None

    monkeypatch.setattr(hub, "fetch_avatar", fake)
    r = await client.get("/api/llm/marketplace/avatars/Qwen")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert (await client.get("/api/llm/marketplace/avatars/nobody")).status_code == 404


def test_current_models_get_the_parsers_vllm_uses_for_them() -> None:
    assert settings.tool_parser_for("Qwen/Qwen3.8-27B-FP8") == "qwen3_xml"
    assert settings.tool_parser_for("Qwen/Qwen3.5-9B") == "qwen3_xml"
    assert settings.tool_parser_for("Qwen/Qwen3-8B") == "hermes"
    assert settings.tool_parser_for("google/gemma-4-31B-it") == "gemma4"
    assert settings.reasoning_parser_for("Qwen/Qwen3.8-27B") == "qwen3"


def test_the_recommended_list_has_the_current_generation() -> None:
    repos = {c.repo_id: c for c in curated.CURATED}
    assert "Qwen/Qwen3.8-27B-FP8" in repos
    assert repos["Qwen/Qwen3.8-27B-FP8"].verified is not None, "served on the DGX pair"
    assert all(c.group in curated.GROUPS for c in curated.CURATED)
    assert all(c.blurb.startswith("marketplace.blurb.") for c in curated.CURATED)


def test_only_the_weights_vllm_loads_are_counted() -> None:
    names = ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors",
             "original/model.safetensors", "metal/model.bin", "config.json"]
    assert hub.loaded_weight_files(names) == names[:2]
    assert hub.has_extra_weight_copies(names)
    # A Mistral repo with its own format and the Hugging Face shards side by side.
    assert hub.loaded_weight_files(["consolidated.safetensors", "model-00001-of-00001.safetensors"]) == [
        "model-00001-of-00001.safetensors",
    ]
    assert hub.loaded_weight_files(["consolidated.safetensors"]) == ["consolidated.safetensors"]
    assert not hub.has_extra_weight_copies(["model.safetensors", "config.json"])


def test_a_repo_with_a_second_copy_is_sized_from_its_own_files() -> None:
    """Found live: gpt-oss-20b came out at 22.7 GB, as the Hub's dtype counts include original/.

    It was "too large" for a 24 GB card that holds its 13.8 GB of weights.
    """

    class _Paths:
        asked: ClassVar[list[list[str]]] = []

        def get_paths_info(self, repo: str, paths: list[str]) -> list[SimpleNamespace]:
            self.asked.append(paths)
            return [SimpleNamespace(path=p, size=7_000_000_000) for p in paths]

    data = {
        "id": "openai/gpt-oss-20b",
        "tags": [], "config": {}, "library_name": "transformers",
        "safetensors_parameters": {"BF16": 1_804_459_584, "U8": 19_110_297_600},
        "siblings": [{"name": "model-00001-of-00002.safetensors", "size": None},
                     {"name": "model-00002-of-00002.safetensors", "size": None},
                     {"name": "original/model.safetensors", "size": None}],
    }
    api = _Paths()
    hub.fill_weight_sizes(api, data)
    assert api.asked == [["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]]
    assert hub.card_from_dict(data)["weights_bytes"] == 14_000_000_000

    plain = {**data, "id": "Qwen/Qwen3-8B", "siblings": [{"name": "model.safetensors", "size": None}]}
    api.asked.clear()
    hub.fill_weight_sizes(api, plain)
    assert api.asked == [], "one copy: the dtype counts are right, no extra call"


async def test_a_dropped_connection_is_tried_once_more() -> None:
    """Found live: resets from this network made one host dialog read "Size unknown"."""
    import requests

    attempts: list[int] = []

    def flaky() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.exceptions.ConnectionError("Connection aborted: connection reset by peer")
        return "answer"

    assert await hub.HubClient._call(flaky) == "answer"  # noqa: SLF001
    assert len(attempts) == 2

    def down() -> str:
        raise requests.exceptions.ConnectionError("Connection aborted: connection reset by peer")

    with pytest.raises(hub.HubUnavailable):
        await hub.HubClient._call(down)  # noqa: SLF001


async def test_the_host_dialog_keeps_the_size_the_list_already_had(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from llm_port_backend.db.models.users import User, current_active_user

    user = MagicMock(spec=User)
    user.id, user.is_active, user.is_superuser, user.is_verified = uuid.uuid4(), True, True, True
    fastapi_app.dependency_overrides[current_active_user] = lambda: user

    async def unreachable(self: Any, repo_id: str) -> dict[str, Any]:
        raise hub.HubUnavailable("connection reset")

    monkeypatch.setattr(hub.HubClient, "detail", unreachable)
    hub._cache.put("card:False:Qwen/Qwen3-0.6B", {  # noqa: SLF001 - what the recommended list fetched
        "repo_id": "Qwen/Qwen3-0.6B", "name": "Qwen3-0.6B", "capabilities": ["tools"], "task": "chat",
        "runnable": True, "weights_bytes": 1_503_300_328,
    }, 600)

    r = await client.get("/api/llm/marketplace/models/Qwen/Qwen3-0.6B")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hub"] == "offline"
    assert body["model"]["weights_bytes"] == 1_503_300_328
