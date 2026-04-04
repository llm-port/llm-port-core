from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class Usage:
    """Normalized usage counters."""

    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


def usage_from_payload(payload: dict[str, Any]) -> Usage:
    """Extract token usage from standard OpenAI-compatible payloads."""
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return Usage(prompt_tokens=None, completion_tokens=None, total_tokens=None)
    return Usage(
        prompt_tokens=_as_int(raw.get("prompt_tokens")),
        completion_tokens=_as_int(raw.get("completion_tokens")),
        total_tokens=_as_int(raw.get("total_tokens")),
    )


def estimate_input_tokens(input_obj: Any) -> int:
    """Cheap heuristic used for MVP TPM checks before upstream response is known."""
    if input_obj is None:
        return 1
    if isinstance(input_obj, str):
        return max(len(input_obj) // 4, 1)
    if isinstance(input_obj, list):
        return max(sum(estimate_input_tokens(chunk) for chunk in input_obj), 1)
    if isinstance(input_obj, dict):
        return max(sum(estimate_input_tokens(v) for v in input_obj.values()), 1)
    return 1


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
