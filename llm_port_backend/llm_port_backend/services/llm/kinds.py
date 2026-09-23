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
