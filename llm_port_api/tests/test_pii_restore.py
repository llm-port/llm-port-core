"""Tokenized values are put back into the answer, streamed or whole."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from llm_port_api.services.gateway.pii_restore import redact_tokens, restore_payload, restore_sse

MAPPING = {"[PERSON_1]": "Alice Meyer", "[EMAIL_ADDRESS_1]": "alice@example.com"}


def _event(content: str | None = None, finish: str | None = None, **extra: Any) -> bytes:
    delta = {} if content is None else {"content": content}
    body = {"id": "c", "object": "chat.completion.chunk", "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
    return f"data: {json.dumps(body)}\n\n".encode()


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def _collect(chunks: tuple[bytes, ...], mapping: dict[str, str] | None = MAPPING) -> tuple[str, list[bytes]]:
    out = [c async for c in restore_sse(_stream(*chunks), mapping)]
    text = []
    for raw in b"".join(out).decode().split("\n\n"):
        if raw.startswith("data: ") and raw != "data: [DONE]":
            for choice in json.loads(raw[6:]).get("choices", []):
                text.append((choice.get("delta") or {}).get("content") or "")
    return "".join(text), out


@pytest.mark.anyio
async def test_a_token_split_across_chunks_comes_back_whole() -> None:
    text, _ = await _collect((_event("Hello [PER"), _event("SON_1], how"), _event(" are you?"),
                              _event(finish="stop"), b"data: [DONE]\n\n"))
    assert text == "Hello Alice Meyer, how are you?"


@pytest.mark.anyio
async def test_no_piece_of_a_token_is_sent_before_it_is_complete() -> None:
    _, out = await _collect((_event("Mail [EMAIL_ADD"), _event("RESS_1] now"), b"data: [DONE]\n\n"))
    assert b"EMAIL" not in b"".join(out)


@pytest.mark.anyio
async def test_a_bracket_that_is_no_token_is_not_lost() -> None:
    text, _ = await _collect((_event("See [1] and [the notes"), _event(" below]"), _event(finish="stop"),
                              b"data: [DONE]\n\n"))
    assert text == "See [1] and [the notes below]"


@pytest.mark.anyio
async def test_what_is_held_goes_out_when_the_answer_ends() -> None:
    """The model stopped in the middle of something that looked like a token."""
    text, _ = await _collect((_event("Ends with [PERS"), b"data: [DONE]\n\n"))
    assert text == "Ends with [PERS"

    text, _ = await _collect((_event("Ends with [PERS"), _event(finish="length")))
    assert text == "Ends with [PERS"

    text, _ = await _collect((_event("No DONE [PERS"),))
    assert text == "No DONE [PERS"


@pytest.mark.anyio
async def test_the_rest_of_each_event_is_kept() -> None:
    _, out = await _collect((_event("Hi [PERSON_1]", usage=None),
                             _event(finish="stop", usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}),
                             b"data: [DONE]\n\n"))
    last = json.loads(out[-2].decode()[6:])
    assert last["usage"]["total_tokens"] == 5
    assert last["choices"][0]["finish_reason"] == "stop"
    assert out[-1] == b"data: [DONE]\n\n"


@pytest.mark.anyio
async def test_without_a_mapping_the_stream_is_untouched() -> None:
    chunks = (_event("Hi [PERSON_1]"), b"data: [DONE]\n\n")
    _, out = await _collect(chunks, mapping=None)
    assert out == list(chunks)


def test_a_whole_answer_is_restored() -> None:
    payload = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi [PERSON_1]"}}], "usage": {}}
    assert restore_payload(payload, MAPPING)["choices"][0]["message"]["content"] == "Hi Alice Meyer"
    assert payload["choices"][0]["message"]["content"] == "Hi [PERSON_1]", "the original is not changed"


def test_a_tokenized_request_becomes_the_redacted_one() -> None:
    request = {"model": "m", "input": ["Mail [EMAIL_ADDRESS_1]"], "messages": [
        {"role": "user", "content": "I am [PERSON_1]"},
        {"role": "user", "content": [{"type": "text", "text": "Hi [PERSON_1]"}, {"type": "image_url", "image_url": {}}]},
    ]}
    out = redact_tokens(request, MAPPING)
    assert out["messages"][0]["content"] == "I am <PERSON>"
    assert out["messages"][1]["content"][0]["text"] == "Hi <PERSON>"
    assert out["messages"][1]["content"][1] == {"type": "image_url", "image_url": {}}
    assert out["input"] == ["Mail <EMAIL_ADDRESS>"]
    assert request["messages"][0]["content"] == "I am [PERSON_1]", "the original is not changed"
