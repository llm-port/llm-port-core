"""The gateway's steps, switched on together.

Each step -- RAG, session history, PII, streaming -- has tests of its own.
These run them in the same request, where the order between them matters.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.models.gateway import (
    ChatMessage,
    ChatSession,
    LLMModelAlias,
    LLMPoolMembership,
    LLMProviderInstance,
    PrivacyMode,
    ProviderHealthStatus,
    ProviderType,
    TenantLLMPolicy,
)
from llm_port_api.services.gateway.lease import LeaseManager
from llm_port_api.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from llm_port_api.services.gateway.observability import GatewayTraceContext
from llm_port_api.services.gateway.pii_client import PIIClient, SanitizeResult
from llm_port_api.services.gateway.rag_lite_client import RagLiteClient
from llm_port_api.services.gateway.skills_client import ResolvedSkill, SkillResolveResult, SkillsClient
from llm_port_api.services.registry import service_registry
from llm_port_api.settings import settings

TEST_JWT_SECRET = "test-secret-32-bytes-minimum-value"
ALIAS = "qwen3-32b"


def _token() -> str:
    return jwt.encode({"sub": "user-1", "tenant_id": "tenant-a"}, TEST_JWT_SECRET, algorithm="HS256")


class _Observability:
    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def start_request_trace(self, **kwargs: object) -> GatewayTraceContext:
        self.payloads.append(kwargs.get("payload"))
        return GatewayTraceContext(
            trace_id="t", observation=None, endpoint="/v1/chat/completions",
            privacy_mode=PrivacyMode.METADATA_ONLY,
        )

    def record_success(self, *_: object, **__: object) -> None: ...
    def record_failure(self, *_: object, **__: object) -> None: ...
    def finalize_stream(self, *_: object, **__: object) -> None: ...


async def _seed(session: AsyncSession, *, provider: ProviderType, pii: dict[str, Any] | None) -> uuid.UUID:
    now = datetime.now(timezone.utc)
    instance_id = uuid.uuid4()
    session.add_all([
        LLMModelAlias(alias=ALIAS, description="", enabled=True, created_at=now, updated_at=now),
        LLMProviderInstance(
            id=instance_id, type=provider, base_url="http://upstream.local", enabled=True,
            weight=1.0, max_concurrency=4, health_status=ProviderHealthStatus.HEALTHY,
            created_at=now, updated_at=now,
        ),
        LLMPoolMembership(model_alias=ALIAS, provider_instance_id=instance_id, enabled=True),
        TenantLLMPolicy(
            tenant_id="tenant-a", privacy_mode=PrivacyMode.METADATA_ONLY,
            allowed_model_aliases=[ALIAS], allowed_provider_types=[provider.value],
            rpm_limit=100, tpm_limit=1_000_000, pii_config=pii, created_at=now, updated_at=now,
        ),
    ])
    await session.commit()
    return instance_id


def _pii(mode: str) -> dict[str, Any]:
    return {
        "telemetry": {"enabled": False},
        "egress": {"enabled_for_cloud": True, "enabled_for_local": True, "mode": mode, "fail_action": "block"},
        "presidio": {"language": "en", "threshold": 0.5, "entities": ["PERSON"]},
    }


async def _session(db: AsyncSession) -> uuid.UUID:
    sess = ChatSession(tenant_id="tenant-a", user_id="user-1", title="t")
    db.add(sess)
    await db.flush()
    return sess.id


async def _history(db: AsyncSession, sid: uuid.UUID) -> list[tuple[str, str]]:
    rows = (await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == sid).order_by(ChatMessage.created_at),
    )).scalars().all()
    return [(m.role, m.content) for m in rows]


class _FakePII:
    """Stands in for the PII service: 'Alice' is a person."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []
        self.modes: list[str] = []

    async def sanitize(self, _self: PIIClient, *, payload: dict[str, Any], policy: Any, mode: str | None = None) -> SanitizeResult:
        self.seen.append(payload)
        text = json.dumps(payload)
        tokenize = (mode or policy.egress.mode) in ("tokenize", "tokenize_reversible")
        self.modes.append("tokenize" if tokenize else "redact")
        replaced = text.replace("Alice", "[PERSON_1]" if tokenize else "<PERSON>")
        return SanitizeResult(
            sanitized_payload=json.loads(replaced), pii_detected="Alice" in text,
            token_mapping={"[PERSON_1]": "Alice"} if tokenize else None,
        )


def _install_pii(monkeypatch: pytest.MonkeyPatch) -> _FakePII:
    fake = _FakePII()
    service_registry.configure("pii", enabled=True, url="http://pii.local")
    monkeypatch.setattr(PIIClient, "sanitize", lambda self, **kw: fake.sanitize(self, **kw))
    return fake


def _chunk(content: str) -> bytes:
    body = {"id": "c", "object": "chat.completion.chunk", "model": ALIAS,
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]}
    return f"data: {json.dumps(body)}\n\n".encode()


def _model_answers(monkeypatch: pytest.MonkeyPatch, answer: str, sent: list[dict[str, Any]]) -> None:
    """The model echoes nothing; it answers *answer*, and what it was sent is kept."""

    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        sent.append(kwargs["payload"])
        if kwargs.get("stream"):
            async def gen() -> AsyncIterator[bytes]:
                for i in range(0, len(answer), 4):
                    yield _chunk(answer[i:i + 4])
                yield b"data: [DONE]\n\n"
            return gen()
        return CompletionResult(status_code=200, payload={
            "id": "c", "object": "chat.completion", "model": ALIAS,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    monkeypatch.setattr(LLMAdapter, "completion", completion)


def _streamed_text(sse: str) -> str:
    out = []
    for line in sse.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            for choice in json.loads(line[6:]).get("choices", []):
                out.append(choice.get("delta", {}).get("content") or "")
    return "".join(out)


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    yield
    service_registry.configure("pii", enabled=False, url=None)
    service_registry.configure("skills", enabled=False, url=None)


# ── PII with streaming ────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_a_tokenized_name_comes_back_to_the_client_as_the_name(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=_pii("tokenize_reversible"))
    fastapi_app.state.gateway_observability = _Observability()
    _install_pii(monkeypatch)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "Hello [PERSON_1], how are you?", sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "messages": [{"role": "user", "content": "I am Alice"}],
    })
    assert r.status_code == 200
    assert "Alice" not in json.dumps(sent[0]), "the model never sees the name"
    text = _streamed_text(r.text) if stream else r.json()["choices"][0]["message"]["content"]
    assert text == "Hello Alice, how are you?"


@pytest.mark.anyio
async def test_a_streamed_answer_is_kept_in_the_session_as_the_client_saw_it(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=_pii("tokenize_reversible"))
    fastapi_app.state.gateway_observability = _Observability()
    _install_pii(monkeypatch)
    _model_answers(monkeypatch, "Hello [PERSON_1]", [])
    sid = await _session(db_session)

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": True, "session_id": str(sid),
        "messages": [{"role": "user", "content": "I am Alice"}],
    })
    assert await _history(db_session, sid) == [("user", "I am Alice"), ("assistant", "Hello Alice")]


# ── RAG and session history ───────────────────────────────────────


def _rag(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]] | None = None, during: Any = None) -> None:
    monkeypatch.setattr(settings, "rag_lite_enabled", True)
    monkeypatch.setattr(settings, "rag_enabled", False)

    async def search(self: RagLiteClient, **kwargs: Any) -> list[dict[str, Any]]:
        if calls is not None:
            calls.append(kwargs)
        if during is not None:
            await during()
        return [{"filename": "handbook.pdf", "chunk_text": "Alice leads the launch on 5 May."}]

    monkeypatch.setattr(RagLiteClient, "search", search)


@pytest.mark.anyio
async def test_retrieved_context_is_not_kept_as_chat_history(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    _rag(monkeypatch)
    _model_answers(monkeypatch, "On 5 May.", [])
    sid = await _session(db_session)

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "session_id": str(sid), "rag": {"top_k": 3},
        "messages": [{"role": "user", "content": "When is the launch?"}],
    })
    assert await _history(db_session, sid) == [("user", "When is the launch?"), ("assistant", "On 5 May.")]


@pytest.mark.anyio
async def test_the_model_sees_the_newest_turns_when_history_is_over_budget(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    monkeypatch.setattr(settings, "session_token_budget", 60)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)
    sid = await _session(db_session)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    for i in range(6):  # ~25 tokens each: only two fit
        db_session.add(ChatMessage(session_id=sid, role="user", content=f"turn {i} " + "x" * 90,
                                   created_at=start + timedelta(minutes=i)))
    await db_session.flush()

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "session_id": str(sid), "messages": [{"role": "user", "content": "and now?"}],
    })
    turns = [m["content"].split()[1] for m in sent[0]["messages"] if m["content"].startswith("turn ")]
    assert turns == ["4", "5"]


# ── PII sees everything that goes to the model ────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_retrieved_context_and_history_are_scanned_before_they_leave(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=_pii("redact"))
    fastapi_app.state.gateway_observability = _Observability()
    _install_pii(monkeypatch)
    _rag(monkeypatch)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)
    sid = await _session(db_session)
    db_session.add(ChatMessage(session_id=sid, role="user", content="Alice asked about it yesterday",
                               created_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
    await db_session.flush()

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "session_id": str(sid), "rag": {"top_k": 3},
        "messages": [{"role": "user", "content": "When is the launch?"}],
    })
    assert r.status_code == 200
    egress = json.dumps(sent[0])
    assert "Alice" not in egress
    assert "launch on 5 May" in egress, "the retrieved context was sent, scanned"
    assert "asked about it yesterday" in egress, "the history was sent, scanned"


# ── What a slot is held for ───────────────────────────────────────


@pytest.mark.anyio
async def test_a_model_slot_is_not_held_while_context_is_gathered(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance_id = await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    held: list[int] = []

    async def look() -> None:
        held.append(await LeaseManager(fastapi_app.state.cache_backend, ttl_sec=90).in_flight(instance_id))

    _rag(monkeypatch, during=look)
    _model_answers(monkeypatch, "ok", [])

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "rag": {"top_k": 3}, "messages": [{"role": "user", "content": "When is the launch?"}],
    })
    assert held == [0]


@pytest.mark.anyio
async def test_the_rag_search_runs_as_the_caller(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backend checks the user's own permission to search.

    Called without a token, it answered 401 every time, and the answer went
    out without its context.
    """
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    searches: list[dict[str, Any]] = []
    _rag(monkeypatch, calls=searches)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "On 5 May.", sent)
    token = _token()

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {token}"}, json={
        "model": ALIAS, "rag": {"top_k": 3, "collection_ids": ["c1"]},
        "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "When is the launch?"}],
    })
    assert searches[0]["api_token"] == token
    assert searches[0]["collection_ids"] == ["c1"]
    roles = [m["role"] for m in sent[0]["messages"]]
    assert sent[0]["messages"][roles.index("user") - 1]["content"].startswith("Use the following retrieved context")
    assert sent[0]["messages"][1]["content"] == "Be brief.", "the client's own system prompt stays first"


# ── Skills ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_skill_reaches_the_model_when_the_skills_module_is_on(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway's registry did not know "skills": no skill ever reached a chat."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    service_registry.configure("skills", enabled=True, url="http://skills.local")
    monkeypatch.setattr(settings, "skills_service_token", "t")
    assert service_registry.get_url("skills") == "http://skills.local"

    async def resolve(self: SkillsClient, **_: Any) -> SkillResolveResult:
        return SkillResolveResult(skills=[ResolvedSkill(
            skill_id="s1", name="Terse", slug="terse", version=2, body_markdown="Answer in five words.",
            priority=1, score=1,
        )])

    async def record(self: SkillsClient, **_: Any) -> None: ...

    monkeypatch.setattr(SkillsClient, "resolve_skills", resolve)
    monkeypatch.setattr(SkillsClient, "record_usage", record)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": "hi"}],
    })
    assert any("Answer in five words." in (m.get("content") or "") for m in sent[0]["messages"])


@pytest.mark.anyio
async def test_a_model_slot_is_not_held_while_pii_scans(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scan -- what leaves, and what the trace keeps from it -- runs before the slot."""
    pii = _pii("tokenize_reversible")
    pii["telemetry"] = {"enabled": True, "mode": "sanitized"}
    instance_id = await _seed(db_session, provider=ProviderType.VLLM, pii=pii)
    fastapi_app.state.gateway_observability = _Observability()
    fake = _install_pii(monkeypatch)
    held: list[int] = []
    scan = fake.sanitize

    async def sanitize_and_look(self: PIIClient, **kw: Any) -> SanitizeResult:
        held.append(await LeaseManager(fastapi_app.state.cache_backend, ttl_sec=90).in_flight(instance_id))
        return await scan(self, **kw)

    monkeypatch.setattr(PIIClient, "sanitize", sanitize_and_look)
    _model_answers(monkeypatch, "ok", [])

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": "I am Alice"}],
    })
    assert held == [0], "one scan, before the slot"


# ── One scan for what leaves and what the trace keeps ─────────────


def _with_telemetry(mode: str, *, local: bool = True) -> dict[str, Any]:
    pii = _pii(mode)
    pii["egress"]["enabled_for_local"] = local
    pii["telemetry"] = {"enabled": True, "mode": "sanitized"}
    return pii


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["tokenize_reversible", "redact"])
async def test_the_trace_gets_the_redacted_copy_from_the_one_scan(
    mode: str, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same text was scanned twice: once to send, once for the trace."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=_with_telemetry(mode))
    observability = fastapi_app.state.gateway_observability = _Observability()
    fake = _install_pii(monkeypatch)
    _model_answers(monkeypatch, "ok", [])

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": "I am Alice"}],
    })
    assert len(fake.seen) == 1
    traced = json.dumps(observability.payloads[0])
    assert "I am <PERSON>" in traced
    assert "Alice" not in traced and "[PERSON_" not in traced


@pytest.mark.anyio
async def test_the_trace_is_still_scanned_when_nothing_leaves_scanned(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local model the policy does not scan for: the trace needs its own scan."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=_with_telemetry("tokenize_reversible", local=False))
    observability = fastapi_app.state.gateway_observability = _Observability()
    fake = _install_pii(monkeypatch)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": "I am Alice"}],
    })
    assert fake.modes == ["redact"]
    assert "Alice" in json.dumps(sent[0]), "sent as it was"
    assert "Alice" not in json.dumps(observability.payloads[0])
