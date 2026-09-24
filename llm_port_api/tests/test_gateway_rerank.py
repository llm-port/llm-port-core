"""/v1/rerank: documents scored against a query by a reranking (scoring) model.

Scoring models were published to the gateway and listed, but a request to
one was refused: "the gateway does not serve requests for yet".
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.models.gateway import LLMProviderInstance, ProviderType
from llm_port_api.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from tests.test_gateway_pipeline_combined import ALIAS, _install_pii, _Observability, _pii, _seed, _token

SCORING = {"task": "scoring"}
DOCS = ["Bananas are yellow.", "Alice leads the launch on 5 May.", "The launch is in Munich."]


class _Reranker:
    """The upstream: scores what it is sent, keeps it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, scores: list[float]) -> None:
        self.calls: list[dict[str, Any]] = []
        reranker = self

        async def rerank(self: LLMAdapter, **kwargs: Any) -> CompletionResult:
            reranker.calls.append(kwargs)
            ranked = sorted(enumerate(scores), key=lambda s: s[1], reverse=True)[: kwargs.get("top_n") or len(scores)]
            return CompletionResult(status_code=200, payload={
                "id": "r1",
                "results": [{"index": i, "relevance_score": s, "document": None} for i, s in ranked],
                "meta": {"billed_units": {"total_tokens": 42}},
            })

        monkeypatch.setattr(LLMAdapter, "rerank", rerank)


async def _rerank(client: AsyncClient, **body: Any) -> Any:
    body.setdefault("model", ALIAS)
    body.setdefault("query", "When is the launch?")
    body.setdefault("documents", DOCS)
    return await client.post("/v1/rerank", headers={"Authorization": f"Bearer {_token()}"}, json=body)


@pytest.mark.anyio
async def test_documents_come_back_scored_with_the_clients_own_text(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=SCORING)
    fastapi_app.state.gateway_observability = _Observability()
    upstream = _Reranker(monkeypatch, [0.01, 0.97, 0.6])

    r = await _rerank(client, top_n=2)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(x["index"], x["relevance_score"]) for x in body["results"]] == [(1, 0.97), (2, 0.6)]
    assert body["results"][0]["document"] == {"text": DOCS[1]}
    assert body["model"] == ALIAS and body["usage"]["total_tokens"] == 42
    assert upstream.calls[0]["query"] == "When is the launch?"
    assert upstream.calls[0]["documents"] == DOCS
    assert upstream.calls[0]["top_n"] == 2


@pytest.mark.anyio
async def test_documents_can_be_left_out_and_given_as_objects(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=SCORING)
    fastapi_app.state.gateway_observability = _Observability()
    upstream = _Reranker(monkeypatch, [0.2, 0.8])

    r = await _rerank(client, documents=[{"text": "a"}, {"text": "b"}], return_documents=False)
    assert r.status_code == 200
    assert all("document" not in x for x in r.json()["results"])
    assert upstream.calls[0]["documents"] == ["a", "b"]


@pytest.mark.anyio
async def test_what_goes_out_is_pii_scanned_and_what_comes_back_is_the_original(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pipeline scans ``messages`` and ``input``; query and documents went out as they were."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=_pii("tokenize_reversible"),
                node_metadata=SCORING)
    fastapi_app.state.gateway_observability = _Observability()
    _install_pii(monkeypatch)
    upstream = _Reranker(monkeypatch, [0.1, 0.9, 0.3])

    r = await _rerank(client, query="What does Alice lead?")
    assert r.status_code == 200, r.text
    sent = upstream.calls[0]
    assert "Alice" not in sent["query"] and "[PERSON_1]" in sent["query"]
    assert "Alice" not in sent["documents"][1]
    assert r.json()["results"][0]["document"] == {"text": DOCS[1]}, "the client's text, not the tokens"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "node_metadata", "advice"),
    [
        ("/v1/rerank", {"task": "chat"}, "is a chat model"),
        ("/v1/embeddings", SCORING, "send it to /v1/rerank"),
    ],
)
async def test_a_model_of_another_kind_is_refused_in_plain_words(
    endpoint: str, node_metadata: dict[str, Any], advice: str,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=node_metadata)
    fastapi_app.state.gateway_observability = _Observability()
    body = {"model": ALIAS, "query": "q", "documents": ["d"], "input": "x"}
    r = await client.post(endpoint, headers={"Authorization": f"Bearer {_token()}"}, json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "model_kind_mismatch"
    assert advice in r.json()["error"]["message"]


@pytest.mark.anyio
async def test_a_cluster_deployment_is_refused_it_has_no_rerank_api(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=SCORING)
    await db_session.execute(
        update(LLMProviderInstance).where(LLMProviderInstance.id == instance).values(source_kind="inference_deployment"),
    )
    await db_session.commit()
    fastapi_app.state.gateway_observability = _Observability()
    upstream = _Reranker(monkeypatch, [0.5])

    r = await _rerank(client)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "rerank_not_supported"
    assert upstream.calls == []


@pytest.mark.anyio
async def test_a_request_without_documents_is_refused(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=SCORING)
    fastapi_app.state.gateway_observability = _Observability()
    r = await _rerank(client, documents=[])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_a_local_engine_is_reached_as_hosted_vllm(monkeypatch: pytest.MonkeyPatch) -> None:
    """LiteLLM's openai provider, which chat uses, has no rerank."""
    import asyncio

    import litellm

    seen: dict[str, Any] = {}

    class _Response:
        def model_dump(self) -> dict[str, Any]:
            return {"results": []}

    async def arerank(**kwargs: Any) -> _Response:
        seen.update(kwargs)
        return _Response()

    monkeypatch.setattr(litellm, "arerank", arerank)
    result = asyncio.run(LLMAdapter().rerank(
        provider_type=ProviderType.VLLM, base_url="http://node:8000", api_key_encrypted=None,
        litellm_provider=None, litellm_model="qwen3-reranker", extra_params=None,
        requested_model="alias", query="q", documents=["d"],
    ))
    assert result.status_code == 200
    assert seen["model"] == "hosted_vllm/qwen3-reranker"
    assert seen["api_base"] == "http://node:8000/v1"
    assert seen["return_documents"] is False


@pytest.mark.anyio
async def test_a_qwen3_reranker_gets_its_instruction_format_once(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw text to Qwen3-Reranker ranked "Bananas are yellow" first for a launch date."""
    instance = await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=SCORING)
    await db_session.execute(
        update(LLMProviderInstance).where(LLMProviderInstance.id == instance)
        .values(litellm_model="Qwen/Qwen3-Reranker-0.6B"),
    )
    await db_session.commit()
    fastapi_app.state.gateway_observability = _Observability()
    upstream = _Reranker(monkeypatch, [0.1, 0.9, 0.3])

    assert (await _rerank(client)).status_code == 200
    sent = upstream.calls[0]
    assert sent["query"].startswith("<|im_start|>system") and "<Query>: When is the launch?" in sent["query"]
    assert sent["documents"][1].startswith("<Document>: Alice leads")
    assert sent["documents"][1].endswith("</think>\n\n")

    formatted = sent["query"]
    assert (await _rerank(client, query=formatted, documents=sent["documents"])).status_code == 200
    assert upstream.calls[1]["query"] == formatted, "not wrapped a second time"


@pytest.mark.anyio
async def test_another_reranker_gets_the_text_as_it_is(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=SCORING)
    fastapi_app.state.gateway_observability = _Observability()
    upstream = _Reranker(monkeypatch, [0.1, 0.9, 0.3])
    assert (await _rerank(client)).status_code == 200
    assert upstream.calls[0]["query"] == "When is the launch?"
