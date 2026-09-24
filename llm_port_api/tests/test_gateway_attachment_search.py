"""Files attached to a chat, too long to include: named to the model, and searched.

A file over the session's token budget used to be left out without a word;
the model answered as if nothing had been attached.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.models.gateway import AttachmentScope, ChatAttachment, ExtractionStatus, ProviderType
from llm_port_api.services.gateway.attachment_search import AttachmentSearch, SearchableFile, passages, words
from llm_port_api.settings import settings
from tests.test_gateway_knowledge_tools import CAN_CALL_TOOLS, _ask, _call, _Model, _say, _tool_names
from tests.test_gateway_pipeline_combined import _Observability, _seed, _session

FILLER = "The quarterly report lists revenue by region and the costs of each office. " * 8
ANSWER = "The Lisbon office closes on 30 June 2027 and its staff move to Porto."
#: ~50,000 characters: far over the 4,096-token budget.
REPORT = "\n\n".join([FILLER] * 40 + [ANSWER] + [FILLER] * 40)


async def _attach(db: AsyncSession, sid: uuid.UUID, text: str, filename: str = "report.txt") -> None:
    db.add(ChatAttachment(
        tenant_id="tenant-a", user_id="user-1", session_id=sid, filename=filename,
        content_type="text/plain", size_bytes=len(text), storage_key=f"k/{filename}",
        extracted_text=text, extraction_status=ExtractionStatus.COMPLETED, scope=AttachmentScope.SESSION,
    ))
    await db.commit()


def _system_text(payload: dict[str, Any]) -> str:
    return "\n".join(str(m["content"]) for m in payload["messages"] if m["role"] == "system")


# ── The search itself ─────────────────────────────────────────────


def test_passages_end_where_sentences_do() -> None:
    text = " ".join(f"Sentence {i} is about topic {i}." for i in range(200))
    parts = passages(text, size=300)
    assert len(parts) > 1
    assert all(len(p) <= 300 for p in parts)
    assert all(p.endswith(".") for p in parts)
    assert "".join(parts).replace(" ", "").replace("\n", "") == text.replace(" ", "")


def test_words_are_stemmed_and_stop_words_dropped() -> None:
    assert words("The offices are closing; restarting the servers") == ["offic", "clos", "restart", "server"]
    assert words("close") == words("closes") == words("closed") == words("closing") == ["clos"]
    assert words("running runs") == ["run", "run"]


def test_the_passage_with_the_rare_words_comes_first() -> None:
    search = AttachmentSearch([SearchableFile("1", "report.txt", REPORT)])
    found = search.search({"query": "When does the Lisbon office close?"})
    assert ANSWER in found["results"][0]["text"]
    assert found["results"][0]["file"] == "report.txt"


def test_a_search_can_be_kept_to_one_file() -> None:
    files = [SearchableFile("1", "a.txt", "Lisbon is in Portugal."), SearchableFile("2", "b.txt", "Lisbon has trams.")]
    only_b = AttachmentSearch(files).search({"query": "Lisbon", "file": "b.txt"})
    assert [r["file"] for r in only_b["results"]] == ["b.txt"]
    unknown = AttachmentSearch(files).search({"query": "Lisbon", "file": "c.txt"})
    assert {r["file"] for r in unknown["results"]} == {"a.txt", "b.txt"}, "no such file: all of them"


def test_a_search_without_a_query_is_an_error_the_model_is_told() -> None:
    content, is_error = AttachmentSearch([SearchableFile("1", "a.txt", "x")]).run({"query": " "})
    assert is_error and "needs a query" in json.loads(content)["error"]


# ── Through the gateway ───────────────────────────────────────────


@pytest.mark.anyio
async def test_a_file_too_long_to_include_is_searched_by_the_model(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    sid = await _session(db_session)
    await _attach(db_session, sid, REPORT)
    model = _Model(monkeypatch, _call("attachment_search", {"query": "Lisbon office closing date"}), _say("30 June 2027."))

    r = await _ask(client, session_id=str(sid),
                   messages=[{"role": "user", "content": "When does the Lisbon office close?"}])
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "30 June 2027."

    first = model.sent[0]
    assert "attachment_search" in _tool_names(first)
    assert "report.txt" in _system_text(first) and "Search it with the attachment_search tool" in _system_text(first)
    assert FILLER not in _system_text(first), "the file itself was not sent"
    answer = next(m for m in model.sent[1]["messages"] if m["role"] == "tool")
    assert ANSWER in json.loads(answer["content"])["results"][0]["text"]


@pytest.mark.anyio
async def test_a_model_that_cannot_search_gets_the_beginning_marked_as_cut(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata={"task": "chat", "tools": False})
    fastapi_app.state.gateway_observability = _Observability()
    sid = await _session(db_session)
    await _attach(db_session, sid, REPORT)
    model = _Model(monkeypatch, _say("I only see the start of it."))

    assert (await _ask(client, session_id=str(sid))).status_code == 200
    sent = model.sent[0]
    assert "attachment_search" not in _tool_names(sent)
    system = _system_text(sent)
    assert "report.txt -- the first" in system and "the rest did not fit" in system
    excerpt_tokens = len(system) // 4
    assert excerpt_tokens <= settings.session_token_budget // 2 + 100, "half the budget, the history keeps room"


@pytest.mark.anyio
async def test_a_file_that_fits_goes_in_whole_and_no_tool_is_offered(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    sid = await _session(db_session)
    await _attach(db_session, sid, ANSWER, filename="note.txt")
    model = _Model(monkeypatch, _say("30 June 2027."))

    assert (await _ask(client, session_id=str(sid))).status_code == 200
    assert ANSWER in _system_text(model.sent[0])
    assert "attachment_search" not in _tool_names(model.sent[0])


@pytest.mark.anyio
async def test_not_offered_when_the_client_rules_tools_out(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    sid = await _session(db_session)
    await _attach(db_session, sid, REPORT)
    model = _Model(monkeypatch, _say("ok"))

    assert (await _ask(client, session_id=str(sid), tool_choice="none")).status_code == 200
    assert "attachment_search" not in _tool_names(model.sent[0])
    assert "the rest did not fit" in _system_text(model.sent[0])
