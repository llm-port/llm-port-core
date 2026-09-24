"""Text chunker for RAG Lite: chunks end where sentences and paragraphs do.

Chunks are about *max_tokens* tokens (estimated at 4 chars/token). Text is
packed a paragraph at a time, and a paragraph too long for one chunk a
sentence at a time; a sentence longer than a chunk is cut in windows. The
overlap between neighbouring chunks is whole sentences. It used to be
fixed-size windows alone, which cut sentences -- and words -- in two, so the
passage that answered a question could be split across two chunks and match
neither well.

No heading-aware or semantic splitting -- that's an Enterprise feature
(Docling Pro + RAG Pro hierarchical chunking).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple


class Chunk(NamedTuple):
    """A single chunk of text with its 0-based position in the document."""

    index: int
    text: str


@dataclass(frozen=True)
class ChunkerConfig:
    """Tunables for the chunker."""

    max_tokens: int = 512
    overlap_tokens: int = 64

    @property
    def max_chars(self) -> int:
        return self.max_tokens * 4

    @property
    def overlap_chars(self) -> int:
        return self.overlap_tokens * 4


_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=\S)")


def chunk_text(text: str, config: ChunkerConfig | None = None) -> list[Chunk]:
    """Split *text* into chunks. Returns an empty list for blank input."""
    if not text or not text.strip():
        return []
    cfg = config or ChunkerConfig()
    spans = _units(text, cfg)
    chunks: list[Chunk] = []
    current: list[tuple[int, int]] = []

    def emit() -> None:
        piece = text[current[0][0]:current[-1][1]]
        if piece.strip():
            chunks.append(Chunk(index=len(chunks), text=piece))

    for span in spans:
        if current and span[1] - current[0][0] > cfg.max_chars:
            emit()
            current = _overlap(current, cfg.overlap_chars)
            if current and span[1] - current[0][0] > cfg.max_chars:
                current = []
        current.append(span)
    if current:
        emit()
    return chunks


def _units(text: str, cfg: ChunkerConfig) -> list[tuple[int, int]]:
    """The spans chunks are made of: paragraphs, or sentences, or windows."""
    units: list[tuple[int, int]] = []
    for start, end in _pieces(text, 0, len(text), _PARAGRAPH_BREAK):
        if end - start <= cfg.max_chars:
            units.append((start, end))
            continue
        for s_start, s_end in _pieces(text, start, end, _SENTENCE_BREAK):
            if s_end - s_start <= cfg.max_chars:
                units.append((s_start, s_end))
            else:
                units.extend(_windows(s_start, s_end, cfg))
    return units


def _pieces(text: str, start: int, end: int, breaks: re.Pattern[str]) -> list[tuple[int, int]]:
    """*text[start:end]* split at *breaks*, each piece keeping what follows it."""
    pieces, at = [], start
    for match in breaks.finditer(text, start, end):
        pieces.append((at, match.end()))
        at = match.end()
    if at < end:
        pieces.append((at, end))
    return pieces


def _windows(start: int, end: int, cfg: ChunkerConfig) -> list[tuple[int, int]]:
    """Fixed windows over a span with no break to cut at."""
    step = max(cfg.max_chars - cfg.overlap_chars, 1)
    return [(at, min(at + cfg.max_chars, end)) for at in range(start, end, step)]


def _overlap(units: list[tuple[int, int]], budget: int) -> list[tuple[int, int]]:
    """The last whole units of a chunk that fit the overlap, to start the next."""
    kept: list[tuple[int, int]] = []
    for span in reversed(units):
        if units[-1][1] - span[0] > budget:
            break
        kept.insert(0, span)
    return kept
