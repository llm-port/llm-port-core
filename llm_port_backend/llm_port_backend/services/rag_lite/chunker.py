"""Fixed-size sliding-window text chunker for RAG Lite.

Splits text into overlapping windows of approximately *max_tokens* tokens
(estimated at 4 chars/token).  No heading-aware or semantic splitting —
that's an Enterprise feature (Docling Pro + RAG Pro hierarchical chunking).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple


class Chunk(NamedTuple):
    """A single chunk of text with its 0-based position in the document."""

    index: int
    text: str


@dataclass(frozen=True)
class ChunkerConfig:
    """Tunables for the sliding-window chunker."""

    max_tokens: int = 512
    overlap_tokens: int = 64

    @property
    def max_chars(self) -> int:
        return self.max_tokens * 4

    @property
    def overlap_chars(self) -> int:
        return self.overlap_tokens * 4


def chunk_text(text: str, config: ChunkerConfig | None = None) -> list[Chunk]:
    """Split *text* into overlapping windows.

    Returns an empty list for blank / whitespace-only input.
    """
    if not text or not text.strip():
        return []

    cfg = config or ChunkerConfig()
    max_chars = cfg.max_chars
    overlap = cfg.overlap_chars
    step = max(max_chars - overlap, 1)

    chunks: list[Chunk] = []
    start = 0
    idx = 0
    while start < len(text):
        end = start + max_chars
        chunk_text_slice = text[start:end]
        if chunk_text_slice.strip():
            chunks.append(Chunk(index=idx, text=chunk_text_slice))
            idx += 1
        start += step

    return chunks
