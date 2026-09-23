"""A chat's history is analysed once, not on every turn.

The gateway sends the whole conversation on each turn, and every message was
analysed again -- one spaCy run and one thread hop each, about 15 ms apiece.
Uses the real Presidio engines and spaCy model.
"""

from __future__ import annotations

from typing import Any

import pytest

from llm_port_pii.services.pii.service import DEFAULT_ENTITIES, PIIService


@pytest.fixture(scope="module")
def service() -> PIIService:
    return PIIService.create()


def _chat(*texts: str) -> dict[str, Any]:
    roles = ("user", "assistant")
    return {"model": "m", "messages": [{"role": roles[i % 2], "content": t} for i, t in enumerate(texts)]}


HISTORY = (
    "Hi, I am Alice Meyer and I live in Munich.",
    "Nice to meet you, Alice Meyer. How can I help?",
    "Please email bob.stein@example.com about the launch.",
)


def _count_analysed(service: PIIService, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    batches: list[list[str]] = []
    real = service._batch.analyze_iterator

    def spy(*, texts: list[str], **kwargs: Any) -> Any:
        batches.append(list(texts))
        return real(texts=texts, **kwargs)

    monkeypatch.setattr(service._batch, "analyze_iterator", spy)
    return batches


@pytest.mark.anyio
async def test_the_same_result_as_analysing_each_message_on_its_own(service: PIIService) -> None:
    """What the service did before: Presidio on each text by itself."""
    result = await service.sanitize_payload(_chat(*HISTORY), mode="redact")
    for original, sanitized in zip(HISTORY, result.payload["messages"], strict=True):
        found = service._analyzer.analyze(
            text=original, language="en", entities=DEFAULT_ENTITIES, score_threshold=0.35,
        )
        assert sanitized["content"] == service._anonymizer.anonymize(text=original, analyzer_results=found).text


@pytest.mark.anyio
async def test_tokenized_messages_restore_to_the_originals(service: PIIService) -> None:
    result = await service.sanitize_payload(_chat(*HISTORY), mode="tokenize")
    assert result.token_mapping
    for original, sanitized in zip(HISTORY, result.payload["messages"], strict=True):
        assert "Alice Meyer" not in sanitized["content"]
        assert "bob.stein@example.com" not in sanitized["content"]
        restored = sanitized["content"]
        for token, value in result.token_mapping.items():
            restored = restored.replace(token, value)
        assert restored == original


@pytest.mark.anyio
async def test_only_the_new_message_is_analysed_on_the_next_turn(
    service: PIIService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    batches = _count_analysed(service, monkeypatch)
    first = await service.sanitize_payload(_chat(*HISTORY, "When is it?"), mode="tokenize")
    second = await service.sanitize_payload(_chat(*HISTORY, "When is it?", "And where, Alice Meyer?"), mode="tokenize")

    assert len(batches) == 2
    assert batches[1] == ["And where, Alice Meyer?"], "the history came from what was analysed before"
    assert second.token_mapping is not None and first.token_mapping is not None
    # Tokens are numbered per request, and the same value is the same token throughout it.
    token = next(t for t, v in second.token_mapping.items() if v == "Alice Meyer")
    assert second.payload["messages"][0]["content"].count(token) == 1
    assert second.payload["messages"][-1]["content"] == f"And where, {token}?"


@pytest.mark.anyio
async def test_new_texts_are_analysed_together(service: PIIService, monkeypatch: pytest.MonkeyPatch) -> None:
    batches = _count_analysed(service, monkeypatch)
    await service.sanitize_payload(_chat("Carol Danvers called.", "Dave Lister replied.", "Carol Danvers called."))
    assert batches == [["Carol Danvers called.", "Dave Lister replied."]], "one batch, and a repeat only once"


@pytest.mark.anyio
async def test_a_setting_change_is_a_new_analysis(service: PIIService, monkeypatch: pytest.MonkeyPatch) -> None:
    batches = _count_analysed(service, monkeypatch)
    await service.sanitize_payload(_chat("Erin Hale lives in Oslo."), entities=["PERSON"])
    await service.sanitize_payload(_chat("Erin Hale lives in Oslo."), entities=["PERSON", "LOCATION"])
    assert len(batches) == 2


def test_the_cache_keeps_only_the_most_recent() -> None:
    from llm_port_pii.services.pii.service import _AnalysisCache

    cache = _AnalysisCache(2)
    cache.put(("a",), ())
    cache.put(("b",), ())
    cache.get(("a",))  # used: now the most recent
    cache.put(("c",), ())
    assert ("a",) in cache and ("c",) in cache and ("b",) not in cache
