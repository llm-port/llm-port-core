"""What a served model is for -- chat, embeddings, scoring -- as its route tells the gateway.

The gateway lists each model with its kind, the chat screen offers only chat
models, and a request of the wrong kind is refused up front. That only holds
if every route says what it is, so every publisher asks here.
"""

from __future__ import annotations

import re
from typing import Any

CHAT, EMBEDDINGS, SCORING = "chat", "embeddings", "scoring"

#: vLLM's ``--task`` / ``--runner`` / ``--convert`` values, in LLM.Port's words.
_VLLM_TASKS = {
    "generate": CHAT,
    "embed": EMBEDDINGS,
    "embedding": EMBEDDINGS,
    "pooling": EMBEDDINGS,
    "score": SCORING,
    "classify": SCORING,
    "reward": SCORING,
    "rerank": SCORING,
}

_RAW_FLAG = re.compile(r"--(task|runner|convert)[=\s]+([A-Za-z_]+)")


def runtime_kind(runtime: Any) -> str:
    """The kind of a runtime LLM.Port runs, from its vLLM flags; chat otherwise.

    Three places can hold the flag: ``generic_config``, the flags the runtime
    page edits (``provider_config.engine_args``), and raw ``extra_args``.
    Without one, vLLM generates text, and LLM.Port runtimes are chat.
    """
    generic = getattr(runtime, "generic_config", None) or {}
    provider = getattr(runtime, "provider_config", None) or {}
    engine_args = {
        str(k).lstrip("-").replace("-", "_"): v for k, v in (provider.get("engine_args") or {}).items()
    }
    for source in (engine_args, generic):
        for name in ("task", "runner", "convert"):
            value = str(source.get(name) or "").lower()
            if value in _VLLM_TASKS:
                return _VLLM_TASKS[value]
    match = _RAW_FLAG.search(str(provider.get("extra_args") or ""))
    if match and match.group(2).lower() in _VLLM_TASKS:
        return _VLLM_TASKS[match.group(2).lower()]
    return CHAT


# ── Tool calls ────────────────────────────────────────────────────────
#
# vLLM answers with tool calls only when started with
# ``--enable-auto-tool-choice`` and a ``--tool-call-parser`` for the model's
# format; without them a request that offers tools is refused (400). So the
# gateway offers its own tools -- knowledge search -- only to routes that
# say they can take them, and every publisher asks here.

_TOOL_FLAGS = re.compile(r"--enable-auto-tool-choice\b")
_PARSER_FLAG = re.compile(r"--tool-call-parser[=\s]+\S+")


def _flag_on(value: Any) -> bool:
    """A flag's value: set bare (``True`` or ``""``) or to a word for yes."""
    if value is None or value is False:
        return False
    return value is True or str(value).strip().lower() in {"", "1", "true", "yes", "on"}


def calls_tools(flags: dict[str, Any] | None) -> bool:
    """Whether vLLM flags (as a dict, dashes or underscores) turn tool calls on."""
    normalized = {str(k).lstrip("-").replace("-", "_"): v for k, v in (flags or {}).items()}
    return _flag_on(normalized.get("enable_auto_tool_choice")) and bool(normalized.get("tool_call_parser"))


def argv_calls_tools(args: list[str] | str | None) -> bool:
    """Whether a vLLM command line turns tool calls on."""
    line = " ".join(args) if isinstance(args, list) else str(args or "")
    return bool(_TOOL_FLAGS.search(line) and _PARSER_FLAG.search(line))


def runtime_calls_tools(runtime: Any) -> bool:
    """Whether a runtime LLM.Port runs has tool calls on, from its vLLM flags."""
    generic = getattr(runtime, "generic_config", None) or {}
    provider = getattr(runtime, "provider_config", None) or {}
    return (
        calls_tools(provider.get("engine_args"))
        or calls_tools(generic)
        or argv_calls_tools(provider.get("extra_args"))
    )


def deployment_calls_tools(spec: dict[str, Any] | None) -> bool:
    """Whether a cluster deployment has tool calls on (``engine.config``)."""
    engine = (spec or {}).get("engine") or {}
    return calls_tools(engine.get("config"))
