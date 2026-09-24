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
    # The current generation (checked on the Hub 2026-09-24: Qwen3.5/3.6/3.8, Gemma 4,
    # gpt-oss, Qwen3-Coder-Next), plus the small models a first deployment wants.
    # ── to try things out ──
    Curated("Qwen/Qwen2.5-0.5B-Instruct", "start", "marketplace.blurb.tiny_verified",
            verified={"on": "DGX Spark pair (2 × GB10)", "date": "2026-09-21"}, params_b=0.5),
    Curated("Qwen/Qwen3-0.6B", "start", "marketplace.blurb.tiny",
            # Hosted from the marketplace, sharing a GB10 at a tenth of it.
            verified={"on": "DGX Spark pair (2 × GB10), 0.1 of one GB10", "date": "2026-09-24"},
            params_b=0.6),
    Curated("Qwen/Qwen3.5-4B", "start", "marketplace.blurb.small_capable", params_b=4.7),
    Curated("microsoft/Phi-4-mini-instruct", "start", "marketplace.blurb.small_capable", params_b=3.8),
    # ── general chat ──
    Curated("Qwen/Qwen3.5-9B", "chat", "marketplace.blurb.all_rounder", params_b=9.7),
    Curated("meta-llama/Llama-3.1-8B-Instruct", "chat", "marketplace.blurb.all_rounder", params_b=8.0),
    Curated("Qwen/Qwen3.8-27B-FP8", "chat", "marketplace.blurb.flagship_fp8",
            # Served in production on the pair, split across both GB10s (preflight 2026-09-19).
            verified={"on": "DGX Spark pair (2 × GB10)", "date": "2026-09-19"}, params_b=27.8),
    Curated("Qwen/Qwen3.8-27B", "chat", "marketplace.blurb.flagship", params_b=27.8),
    Curated("Qwen/Qwen3.6-35B-A3B-FP8", "chat", "marketplace.blurb.moe_fast", params_b=36.0),
    Curated("google/gemma-4-26B-A4B-it", "chat", "marketplace.blurb.gemma", params_b=25.8),
    Curated("mistralai/Mistral-Small-3.2-24B-Instruct-2506", "chat", "marketplace.blurb.mistral_small",
            settings={"tokenizer_mode": "mistral", "config_format": "mistral", "load_format": "mistral"},
            capabilities=("tools",), params_b=24.0),
    Curated("meta-llama/Llama-3.3-70B-Instruct", "chat", "marketplace.blurb.large_dense", params_b=70.6),
    # ── code ──
    Curated("Qwen/Qwen3-Coder-30B-A3B-Instruct", "code", "marketplace.blurb.coder", params_b=30.5),
    Curated("Qwen/Qwen3-Coder-Next-FP8", "code", "marketplace.blurb.coder_large", params_b=79.7),
    # ── reasoning ──
    Curated("openai/gpt-oss-20b", "reasoning", "marketplace.blurb.gpt_oss_small", capabilities=("tools",),
            params_b=20.0),
    Curated("openai/gpt-oss-120b", "reasoning", "marketplace.blurb.gpt_oss_large", capabilities=("tools",),
            params_b=120.0),
    # ── images ──
    Curated("Qwen/Qwen3-VL-8B-Instruct", "vision", "marketplace.blurb.vision", params_b=8.8),
    Curated("google/gemma-4-31B-it", "vision", "marketplace.blurb.gemma", params_b=31.3),
    # ── embeddings (for knowledge bases) ──
    Curated("Qwen/Qwen3-Embedding-0.6B", "embedding", "marketplace.blurb.embedding_small", params_b=0.6),
    Curated("Qwen/Qwen3-Embedding-8B", "embedding", "marketplace.blurb.embedding_large", params_b=7.6),
)

GROUPS = ("start", "chat", "code", "reasoning", "vision", "embedding")

BY_REPO = {c.repo_id: c for c in CURATED}


def curated_for(repo_id: str) -> Curated | None:
    return BY_REPO.get(repo_id)
