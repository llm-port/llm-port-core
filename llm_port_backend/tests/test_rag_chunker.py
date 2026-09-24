"""Unit tests for the RAG Lite ``chunk_text`` chunker.

The estimator is 4 chars/token: ``max_chars = 4 * max_tokens`` and
``overlap_chars = 4 * overlap_tokens``. Chunks are packed from paragraphs,
then sentences; text with no break to cut at is cut in windows that advance
by ``max(max_chars - overlap_chars, 1)``.
"""

from __future__ import annotations

from llm_port_backend.services.rag_lite.chunker import Chunk, ChunkerConfig, chunk_text


def test_blank_input_returns_empty_list() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_short_text_single_chunk() -> None:
    chunks = chunk_text("hello world")
    assert len(chunks) == 1
    assert chunks[0] == Chunk(index=0, text="hello world")


def test_config_char_properties() -> None:
    cfg = ChunkerConfig(max_tokens=10, overlap_tokens=2)
    assert cfg.max_chars == 40
    assert cfg.overlap_chars == 8
    assert isinstance(cfg, ChunkerConfig)


def test_windows_slide_by_max_minus_overlap() -> None:
    cfg = ChunkerConfig(max_tokens=5, overlap_tokens=1)  # max 20, overlap 4, step 16
    text = "x" * 48  # windows at 0..20, 16..36, 32..48 → 3 chunks
    chunks = chunk_text(text, cfg)
    assert [c.index for c in chunks] == [0, 1, 2]
    assert [c.text for c in chunks] == ["x" * 20, "x" * 20, "x" * 16]


def test_consecutive_windows_share_overlap_region() -> None:
    cfg = ChunkerConfig(max_tokens=4, overlap_tokens=2)  # max 16, overlap 8, step 8
    chunks = chunk_text("a" * 32, cfg)  # 0..16, 8..24, 16..32
    assert [len(c.text) for c in chunks] == [16, 16, 16]
    ov = cfg.overlap_chars  # 8
    for prev, cur in zip(chunks, chunks[1:]):
        assert prev.text[-ov:] == cur.text[:ov]
    assert chunks[-1].text.endswith("a") and "".join(c.text[:8] for c in chunks) + chunks[-1].text[8:] == "a" * 32


def test_whitespace_only_window_is_skipped_and_index_not_consumed() -> None:
    cfg = ChunkerConfig(max_tokens=4, overlap_tokens=0)  # max 16, step 16
    text = ("w" * 16) + (" " * 32) + ("v" * 16)
    chunks = chunk_text(text, cfg)
    # Middle windows fall entirely inside the blank run and are dropped.
    assert [c.index for c in chunks] == [0, 1]
    assert chunks[0].text == "w" * 16
    assert chunks[1].text == "v" * 16


def test_no_chunk_is_a_tail_of_the_one_before() -> None:
    """The window chunker added tails already inside the previous chunk."""
    cfg = ChunkerConfig(max_tokens=2, overlap_tokens=2)  # step = max(8 - 8, 1) = 1
    assert [c.text for c in chunk_text("abcde", cfg)] == ["abcde"]
    cfg = ChunkerConfig(max_tokens=3, overlap_tokens=1)  # max 12, step 8
    assert [len(c.text) for c in chunk_text("y" * 20, cfg)] == [12, 12]
    assert [len(c.text) for c in chunk_text("y" * 25, cfg)] == [12, 12, 9]


# ── Chunks end where sentences and paragraphs do ──────────────────


SENTENCES = [f"Sentence number {i} says something about topic {i}." for i in range(12)]


def test_a_sentence_is_not_cut_in_two() -> None:
    cfg = ChunkerConfig(max_tokens=40, overlap_tokens=0)  # 160 chars
    chunks = chunk_text(" ".join(SENTENCES), cfg)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.strip().endswith("."), chunk.text
        assert chunk.text.lstrip().startswith("Sentence"), chunk.text
    assert all(len(c.text) <= 160 for c in chunks)


def test_paragraphs_are_packed_together_up_to_the_size() -> None:
    cfg = ChunkerConfig(max_tokens=30, overlap_tokens=0)  # 120 chars
    paragraphs = ["First paragraph, short.", "Second paragraph, also short.", "Third one.", "x" * 100]
    chunks = chunk_text("\n\n".join(paragraphs), cfg)
    assert chunks[0].text.startswith("First paragraph") and "Third one." in chunks[0].text
    assert chunks[1].text == "x" * 100


def test_the_overlap_is_whole_sentences() -> None:
    cfg = ChunkerConfig(max_tokens=40, overlap_tokens=15)  # 160 chars, overlap 60
    chunks = chunk_text(" ".join(SENTENCES), cfg)
    for prev, cur in zip(chunks, chunks[1:]):
        first = cur.text.split(".")[0] + "."
        assert first.strip() in prev.text, "the next chunk starts with the last sentence of this one"


def test_a_sentence_longer_than_a_chunk_is_cut_in_windows() -> None:
    cfg = ChunkerConfig(max_tokens=10, overlap_tokens=0)  # 40 chars
    long_one = "word " * 30  # 150 chars, no sentence end
    chunks = chunk_text(f"Short start. {long_one}", cfg)
    assert all(len(c.text) <= 40 for c in chunks)
    assert "".join(c.text for c in chunks).replace(" ", "") == f"Short start. {long_one}".replace(" ", "")
