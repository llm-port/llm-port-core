"""The model looks knowledge up itself, with tools the gateway runs.

Retrieval used to run before the model whenever a request carried ``rag``:
the last user message was searched and the results went in as a system
message, needed or not. Now the model gets ``knowledge_search`` and
``knowledge_open`` -- when its route can take tools -- and the gateway runs
them, as the user, between rounds of the model's answer.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.models.gateway import ProviderType
from llm_port_api.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from llm_port_api.services.gateway.rag_lite_client import RagLiteClient
from llm_port_api.settings import settings
from tests.test_gateway_pipeline_combined import (
    ALIAS,
    _history,
    _install_pii,
    _Observability,
    _pii,
    _seed,
    _session,
    _token,
)

CAN_CALL_TOOLS = {"task": "chat", "tools": True}
HIT = {"filename": "handbook.pdf", "document_id": "d1", "chunk_index": 3, "score": 0.91,
       "chunk_text": "Alice leads the launch on 5 May."}


def _call(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> tuple[str, Any]:
    return ("call", {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}})


def _say(text: str) -> tuple[str, Any]:
    return ("say", text)


class _Model:
    """Answers each round from a script; keeps what it was sent."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *rounds: tuple[str, Any]) -> None:
        self.rounds = list(rounds)
        self.sent: list[dict[str, Any]] = []
        model = self

        async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
            model.sent.append(kwargs["payload"])
            kind, value = model.rounds.pop(0)
            return _stream(kind, value) if kwargs.get("stream") else _whole(kind, value)

        monkeypatch.setattr(LLMAdapter, "completion", completion)


def _whole(kind: str, value: Any) -> CompletionResult:
    message = (
        {"role": "assistant", "content": None, "tool_calls": [value]} if kind == "call"
        else {"role": "assistant", "content": value}
    )
    return CompletionResult(status_code=200, payload={
        "id": "c", "object": "chat.completion", "model": ALIAS,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if kind == "call" else "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    })


def _chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> bytes:
    body = {"id": "c", "object": "chat.completion.chunk", "model": ALIAS,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
    return f"data: {json.dumps(body)}\n\n".encode()


async def _stream(kind: str, value: Any) -> AsyncIterator[bytes]:
    if kind == "call":
        # As vLLM sends one: the name first, the arguments in pieces.
        args = value["function"]["arguments"]
        yield _chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": value["id"], "type": "function",
                                                            "function": {"name": value["function"]["name"], "arguments": ""}}]})
        for piece in (args[: len(args) // 2], args[len(args) // 2:]):
            yield _chunk({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]})
        yield _chunk({}, finish="tool_calls")
    else:
        for i in range(0, len(value), 5):
            yield _chunk({"content": value[i:i + 5]})
        yield _chunk({}, finish="stop", usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})
    yield b"data: [DONE]\n\n"


class _Knowledge:
    """RAG Lite on the backend, as the tools reach it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fails: bool = False) -> None:
        self.searches: list[dict[str, Any]] = []
        self.passages: list[dict[str, Any]] = []
        monkeypatch.setattr(settings, "rag_lite_enabled", True)
        monkeypatch.setattr(settings, "rag_enabled", False)
        knowledge = self

        async def search(self: RagLiteClient, **kwargs: Any) -> list[dict[str, Any]]:
            knowledge.searches.append(kwargs)
            if fails:
                raise RuntimeError("401 Unauthorized")
            return [HIT]

        async def passage(self: RagLiteClient, **kwargs: Any) -> dict[str, Any]:
            knowledge.passages.append(kwargs)
            return {"filename": "handbook.pdf", "chunk_count": 9,
                    "chunks": [{"chunk_index": 2, "text": "Gates: security, load."},
                               {"chunk_index": 3, "text": "Alice leads the launch on 5 May."}]}

        monkeypatch.setattr(RagLiteClient, "search", search)
        monkeypatch.setattr(RagLiteClient, "passage", passage)


def _tool_names(payload: dict[str, Any]) -> list[str]:
    return [t["function"]["name"] for t in payload.get("tools") or []]


def _streamed(raw: str) -> tuple[str, list[dict[str, Any]]]:
    text, events = [], []
    for line in raw.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            event = json.loads(line[6:])
            events.append(event)
            for choice in event.get("choices", []):
                text.append((choice.get("delta") or {}).get("content") or "")
    return "".join(text), events


async def _ask(client: AsyncClient, **body: Any) -> Any:
    body.setdefault("model", ALIAS)
    body.setdefault("messages", [{"role": "user", "content": "When is the launch?"}])
    return await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json=body)


# ── Offered, or not ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_rag_field_is_refused_and_says_why(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    r = await _ask(client, rag={"top_k": 3})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_parameter"
    assert "knowledge_search" in r.json()["error"]["message"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("node_metadata", "provider", "offered"),
    [
        (CAN_CALL_TOOLS, ProviderType.VLLM, True),
        ({"task": "chat", "tools": False}, ProviderType.VLLM, False),
        ({"task": "chat"}, ProviderType.VLLM, False),  # vLLM that says nothing: it may refuse tools
        (None, ProviderType.REMOTE_OPENAI, True),  # a remote API takes tools
    ],
    ids=["says-yes", "says-no", "local-says-nothing", "remote"],
)
async def test_the_tools_go_only_to_models_that_call_tools(
    node_metadata: dict[str, Any] | None, provider: ProviderType, offered: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=provider, pii=None, node_metadata=node_metadata)
    fastapi_app.state.gateway_observability = _Observability()
    _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _say("On 5 May."))

    assert (await _ask(client)).status_code == 200
    assert ("knowledge_search" in _tool_names(model.sent[0])) is offered


@pytest.mark.anyio
async def test_not_offered_when_rag_lite_is_off_or_tools_are_ruled_out(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _say("a"), _say("b"))

    await _ask(client, tool_choice="none")
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    await _ask(client)
    assert [_tool_names(p) for p in model.sent] == [[], []]


# ── Run by the gateway ────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_search_runs_as_the_user_and_its_results_go_back_to_the_model(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "launch date"}), _say("On 5 May [handbook.pdf]."))

    r = await _ask(client)
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "On 5 May [handbook.pdf]."
    assert knowledge.searches[0]["api_token"] == _token()
    assert knowledge.searches[0]["query"] == "launch date"

    second = model.sent[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["function"]["name"] == "knowledge_search"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"
    result = json.loads(second[-1]["content"])
    assert result["results"][0] == {"source": "handbook.pdf", "document_id": "d1", "chunk": 3, "score": 0.91,
                                    "text": "Alice leads the launch on 5 May."}


@pytest.mark.anyio
async def test_a_hit_is_read_further_with_knowledge_open(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_open", {"document_id": "d1", "chunk": 3}), _say("Two gates."))

    await _ask(client)
    assert knowledge.passages == [{"document_id": "d1", "chunk": 3, "around": 2, "api_token": _token()}]
    opened = json.loads(model.sent[1]["messages"][-1]["content"])
    assert opened["source"] == "handbook.pdf"
    assert opened["text"] == "Gates: security, load.\n\nAlice leads the launch on 5 May."


@pytest.mark.anyio
async def test_a_failed_search_is_told_to_the_model(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not "nothing found": the model can say it could not look."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    _Knowledge(monkeypatch, fails=True)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("I could not search."))

    await _ask(client)
    assert "401 Unauthorized" in json.loads(model.sent[1]["messages"][-1]["content"])["error"]


# ── Streamed ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_streamed_answer_shows_only_the_answer_not_the_search(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With tools, a streamed chat used to be answered whole, then replayed."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "launch date"}), _say("On 5 May [handbook.pdf]."))

    r = await _ask(client, stream=True)
    text, events = _streamed(r.text)
    assert text == "On 5 May [handbook.pdf]."
    assert not any("tool_calls" in (c.get("delta") or {}) for e in events for c in e.get("choices", []))
    assert events[-1]["choices"][0]["finish_reason"] == "stop"
    assert r.text.rstrip().endswith("data: [DONE]")
    assert knowledge.searches[0]["query"] == "launch date", "the arguments came in two pieces"
    assert model.sent[1]["messages"][-1]["role"] == "tool"


@pytest.mark.anyio
async def test_a_search_round_is_not_kept_as_chat_history(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    _Knowledge(monkeypatch)
    _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("On 5 May."))
    sid = await _session(db_session)

    await _ask(client, stream=True, session_id=str(sid))
    assert await _history(db_session, sid) == [("user", "When is the launch?"), ("assistant", "On 5 May.")]


# ── A tool the client defined ─────────────────────────────────────

WEATHER = {"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {}}}}


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_a_tool_the_client_defined_goes_back_to_the_client(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("get_weather", {"city": "Munich"}))

    r = await _ask(client, stream=stream, tools=[WEATHER])
    assert len(model.sent) == 1, "not run by the gateway, not asked again"
    assert _tool_names(model.sent[0]) == ["get_weather", "knowledge_search", "knowledge_open"]
    if stream:
        _, events = _streamed(r.text)
        calls = [c["delta"]["tool_calls"] for e in events for c in e.get("choices", []) if "tool_calls" in c["delta"]]
        assert calls[0][0]["function"] == {"name": "get_weather", "arguments": json.dumps({"city": "Munich"})}
        assert events[-1]["choices"][0]["finish_reason"] == "tool_calls"
    else:
        message = r.json()["choices"][0]["message"]
        assert message["tool_calls"][0]["function"]["name"] == "get_weather"


# ── PII ───────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_search_results_use_the_questions_tokens(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name in a result is the token the question used for it, and comes back as the name."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=_pii("tokenize_reversible"))
    fastapi_app.state.gateway_observability = _Observability()
    fake = _install_pii(monkeypatch)
    knowledge = _Knowledge(monkeypatch)
    model = _Model(
        monkeypatch,
        _call("knowledge_search", {"query": "what does [PERSON_1] lead"}),
        _say("[PERSON_1] leads the launch."),
    )

    r = await _ask(client, stream=stream, messages=[{"role": "user", "content": "What does Alice lead?"}])
    text = _streamed(r.text)[0] if stream else r.json()["choices"][0]["message"]["content"]

    assert knowledge.searches[0]["query"] == "what does Alice lead", "searched inside LLM.Port with the name"
    assert fake.continued[-1] == {"[PERSON_1]": "Alice"}, "the result continued the question's tokens"
    sent_back = model.sent[1]["messages"][-1]["content"]
    assert "Alice" not in sent_back and "[PERSON_1] leads the launch" in sent_back
    assert text == "Alice leads the launch."
