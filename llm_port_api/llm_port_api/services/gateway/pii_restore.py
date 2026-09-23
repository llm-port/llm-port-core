"""Put the values PII tokenization took out back into the model's answer.

Tokenize mode sends ``[PERSON_1]`` to the model in place of a name and keeps
the mapping. The answer comes back with the tokens in it, and the client
must get the names. The PII service does this with a plain replacement over
``choices[].message.content`` (``/api/v1/pii/detokenize``); the gateway holds
the mapping already, so it does the same here, without a round trip.

A streamed answer needs more than that, and did not get it: the token mapping
was dropped on the streaming path, so the chat page -- which always streams --
showed "Hello [PERSON_1]". A token can also arrive split across two chunks
(``"[PER"`` then ``"SON_1]"``), so the text that might be the start of one is
held back until the rest arrives or the answer ends.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


def restore_text(text: str, mapping: dict[str, str]) -> str:
    """*text* with every token in *mapping* replaced by its value."""
    for token, value in mapping.items():
        if token in text:
            text = text.replace(token, value)
    return text


def restore_payload(payload: dict[str, Any], mapping: dict[str, str] | None) -> dict[str, Any]:
    """A whole (non-streamed) chat completion, with its answer restored."""
    if not mapping or not isinstance(payload.get("choices"), list):
        return payload
    return {**payload, "choices": [_restore_choice(c, mapping) for c in payload["choices"]]}


def _restore_choice(choice: Any, mapping: dict[str, str]) -> Any:
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        return choice
    content = message.get("content")
    if isinstance(content, str):
        content = restore_text(content, mapping)
    elif isinstance(content, list):
        content = [
            {**part, "text": restore_text(part["text"], mapping)}
            if isinstance(part, dict) and isinstance(part.get("text"), str) else part
            for part in content
        ]
    return {**choice, "message": {**message, "content": content}}


def redact_tokens(payload: dict[str, Any], mapping: dict[str, str] | None) -> dict[str, Any]:
    """A tokenized request with each token as the PII service's redaction.

    ``[PERSON_1]`` becomes ``<PERSON>`` -- what ``mode=redact`` writes -- so
    the copy kept for tracing is the redacted one without scanning the text a
    second time. The mapping is not kept anywhere, so the trace cannot be
    turned back into the values.
    """
    if not mapping:
        return payload
    placeholders = {token: f"<{token[1:-1].rsplit('_', 1)[0]}>" for token in mapping}

    def swap(value: Any) -> Any:
        if isinstance(value, str):
            return restore_text(value, placeholders)
        if isinstance(value, list):
            return [
                {**part, "text": restore_text(part["text"], placeholders)}
                if isinstance(part, dict) and isinstance(part.get("text"), str)
                else swap(part) if isinstance(part, str) else part
                for part in value
            ]
        return value

    out = dict(payload)
    if isinstance(out.get("messages"), list):
        out["messages"] = [
            {**m, "content": swap(m.get("content"))} if isinstance(m, dict) else m
            for m in out["messages"]
        ]
    if "input" in out:
        out["input"] = swap(out["input"])
    return out


class _Held:
    """The text of one choice that may still turn out to be part of a token."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.longest = max(len(t) for t in mapping)
        self.pending = ""

    def feed(self, text: str) -> str:
        """Take the next piece; return what is safe to send now, restored."""
        self.pending += text
        cut = self._unfinished_token_start()
        ready, self.pending = self.pending[:cut], self.pending[cut:]
        return restore_text(ready, self.mapping)

    def flush(self) -> str:
        ready, self.pending = self.pending, ""
        return restore_text(ready, self.mapping)

    def _unfinished_token_start(self) -> int:
        """Where a token that has not been closed yet may begin, else the end."""
        start = self.pending.rfind("[")
        if start == -1 or "]" in self.pending[start:]:
            return len(self.pending)
        if len(self.pending) - start >= self.longest:
            return len(self.pending)  # longer than any token: it is not one
        return start


async def restore_sse(stream: AsyncIterator[bytes], mapping: dict[str, str] | None) -> AsyncIterator[bytes]:
    """A streamed chat completion (SSE), with its answer restored as it goes.

    Events pass through as they are, except for the answer text in
    ``choices[].delta.content``. What is held back goes out with the event
    that ends the choice, or before ``[DONE]``.
    """
    if not mapping:
        async for chunk in stream:
            yield chunk
        return

    held: dict[int, _Held] = {}
    last: dict[str, Any] = {}
    buffer = b""
    async for chunk in stream:
        buffer += chunk
        while b"\n\n" in buffer:
            event, buffer = buffer.split(b"\n\n", 1)
            for out in _restore_event(event, held, mapping, last):
                yield out
    if buffer.strip():
        for out in _restore_event(buffer, held, mapping, last):
            yield out
    for out in _flush(held, last):  # a stream that ended without [DONE]
        yield out


def _restore_event(
    event: bytes, held: dict[int, _Held], mapping: dict[str, str], last: dict[str, Any],
) -> list[bytes]:
    text = event.decode("utf-8", errors="replace").strip()
    if not text.startswith("data:"):
        return [event + b"\n\n"]
    data = text[len("data:"):].strip()
    if data == "[DONE]":
        return [*_flush(held, last), b"data: [DONE]\n\n"]
    try:
        body = json.loads(data)
    except json.JSONDecodeError:
        return [event + b"\n\n"]
    if not isinstance(body, dict) or not isinstance(body.get("choices"), list):
        return [event + b"\n\n"]

    last.update({k: body[k] for k in ("id", "object", "created", "model") if k in body})
    for choice in body["choices"]:
        if not isinstance(choice, dict):
            continue
        index = choice.get("index", 0)
        delta = choice.get("delta")
        state = held.setdefault(index, _Held(mapping))
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            delta["content"] = state.feed(delta["content"])
        if choice.get("finish_reason") is not None and state.pending:
            if not isinstance(delta, dict):
                delta = choice["delta"] = {}
            delta["content"] = (delta.get("content") or "") + state.flush()
    return [f"data: {json.dumps(body)}\n\n".encode()]


def _flush(held: dict[int, _Held], last: dict[str, Any]) -> list[bytes]:
    """An event carrying whatever is still held, for each choice."""
    out = []
    for index, state in held.items():
        if state.pending:
            choice = {"index": index, "delta": {"content": state.flush()}, "finish_reason": None}
            body = {**last, "choices": [choice]}
            out.append(f"data: {json.dumps(body)}\n\n".encode())
    return out
