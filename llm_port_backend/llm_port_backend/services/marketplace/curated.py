"""A short list of models known to serve well on vLLM, grouped by what they are for.

The list is a starting point, not a gate: anything on the Hub can be searched
and hosted. What an entry adds over a search result is a group, a one-line
reason it is here (a translation key; the interface words it), settings that
differ from what would be suggested for its family, and -- only where it was
actually done -- the date and hardware it was served on here.

"Verified" is never set without the record behind it: a user reads it to
decide whether a model will come up on their hardware.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Curated:
    repo_id: str
    #: ``start`` / ``chat`` / ``code`` / ``reasoning`` / ``vision`` / ``embedding``
    group: str
    #: Translation key for the one line saying why it is here.
    blurb: str
    #: Settings on top of the suggested ones.
    settings: dict[str, Any] = field(default_factory=dict)
    #: Capabilities the Hub metadata does not show (Mistral's tokenizer carries its tool template).
    capabilities: tuple[str, ...] = ()
    #: Where it was served here: ``{"on": "DGX Spark pair (2 x GB10)", "date": "2026-09-21"}``.
    verified: dict[str, str] | None = None
    #: Rough size for when the Hub is unreachable.
    params_b: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CURATED: tuple[Curated, ...] = (
    # ── to try things out ──
    Curated("Qwen/Qwen2.5-0.5B-Instruct", "start", "marketplace.blurb.tiny_verified",
            verified={"on": "DGX Spark pair (2 × GB10)", "date": "2026-09-21"}, params_b=0.5),
    Curated("Qwen/Qwen3-0.6B", "start", "marketplace.blurb.tiny", params_b=0.6),
    Curated("Qwen/Qwen3-4B-Instruct-2507", "start", "marketplace.blurb.small_capable", params_b=4.0),
    Curated("microsoft/Phi-4-mini-instruct", "start", "marketplace.blurb.small_capable", params_b=3.8),
    # ── general chat ──
    Curated("Qwen/Qwen3-8B", "chat", "marketplace.blurb.all_rounder", params_b=8.2),
    Curated("meta-llama/Llama-3.1-8B-Instruct", "chat", "marketplace.blurb.all_rounder", params_b=8.0),
    Curated("Qwen/Qwen3-14B", "chat", "marketplace.blurb.stronger", params_b=14.8),
    Curated("openai/gpt-oss-20b", "chat", "marketplace.blurb.gpt_oss_small", capabilities=("tools",), params_b=20.0),
    Curated("Qwen/Qwen3-30B-A3B-Instruct-2507", "chat", "marketplace.blurb.moe_fast", params_b=30.5),
    Curated("Qwen/Qwen3-32B", "chat", "marketplace.blurb.stronger", params_b=32.8),
    Curated("mistralai/Mistral-Small-3.2-24B-Instruct-2506", "chat", "marketplace.blurb.mistral_small",
            settings={"tokenizer_mode": "mistral", "config_format": "mistral", "load_format": "mistral"},
            capabilities=("tools",), params_b=24.0),
    Curated("openai/gpt-oss-120b", "chat", "marketplace.blurb.gpt_oss_large", capabilities=("tools",), params_b=120.0),
    Curated("meta-llama/Llama-3.3-70B-Instruct", "chat", "marketplace.blurb.large_dense", params_b=70.6),
    # ── code ──
    Curated("Qwen/Qwen3-Coder-30B-A3B-Instruct", "code", "marketplace.blurb.coder", params_b=30.5),
    # ── reasoning ──
    Curated("deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", "reasoning", "marketplace.blurb.reasoning", params_b=14.8),
    # ── images ──
    Curated("Qwen/Qwen3-VL-8B-Instruct", "vision", "marketplace.blurb.vision", params_b=8.8),
    Curated("google/gemma-3-27b-it", "vision", "marketplace.blurb.gemma", params_b=27.4),
    # ── embeddings (for knowledge bases) ──
    Curated("Qwen/Qwen3-Embedding-0.6B", "embedding", "marketplace.blurb.embedding_small", params_b=0.6),
    Curated("Qwen/Qwen3-Embedding-8B", "embedding", "marketplace.blurb.embedding_large", params_b=7.6),
)

GROUPS = ("start", "chat", "code", "reasoning", "vision", "embedding")

BY_REPO = {c.repo_id: c for c in CURATED}


def curated_for(repo_id: str) -> Curated | None:
    return BY_REPO.get(repo_id)
