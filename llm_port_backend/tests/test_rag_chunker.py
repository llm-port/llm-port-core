"""Unit tests for the RAG Lite sliding-window ``chunk_text`` chunker.

The estimator is 4 chars/token: ``max_chars = 4 * max_tokens`` and
``overlap_chars = 4 * overlap_tokens``; each window advances by
``max(max_chars - overlap_chars, 1)``.
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
    chunks = chunk_text("a" * 32, cfg)  # 0..16, 8..24, 16..32, 24..32
    assert [len(c.text) for c in chunks] == [16, 16, 16, 8]
    ov = cfg.overlap_chars  # 8
    for prev, cur in zip(chunks, chunks[1:]):
        assert prev.text[-ov:] == cur.text[:ov]


def test_whitespace_only_window_is_skipped_and_index_not_consumed() -> None:
    cfg = ChunkerConfig(max_tokens=4, overlap_tokens=0)  # max 16, step 16
    text = ("w" * 16) + (" " * 32) + ("v" * 16)
    chunks = chunk_text(text, cfg)
    # Middle windows fall entirely inside the blank run and are dropped.
    assert [c.index for c in chunks] == [0, 1]
    assert chunks[0].text == "w" * 16
    assert chunks[1].text == "v" * 16


def test_step_floors_to_one_when_overlap_equals_max() -> None:
    cfg = ChunkerConfig(max_tokens=2, overlap_tokens=2)  # step = max(8 - 8, 1) = 1
    chunks = chunk_text("abcde", cfg)
    # step=1 → a window starting at every offset; each holds the remaining text.
    assert [c.text for c in chunks] == ["abcde", "bcde", "cde", "de", "e"]
    assert [c.index for c in chunks] == [0, 1, 2, 3, 4]


def test_tail_window_is_shorter_than_max() -> None:
    cfg = ChunkerConfig(max_tokens=3, overlap_tokens=1)  # max 12, step 8
    # "y"*20: 0..12(12), 8..20(12), 16..20(4)
    assert [len(c.text) for c in chunk_text("y" * 20, cfg)] == [12, 12, 4]
    # "y"*25: 0..12(12), 8..20(12), 16..25(9), 24..25(1)
    assert [len(c.text) for c in chunk_text("y" * 25, cfg)] == [12, 12, 9, 1]
