"""Tests for clearing the Prometheus multiprocess directory on startup.

Root cause these pin: the directory is cleared with ``rmtree`` and then
recreated with ``mkdir``. When the removal failed -- prometheus-client mmaps
the ``.db`` files and Windows will not delete a mapped file -- the
``ignore_errors=True`` flag hid it and ``mkdir`` raised
``FileExistsError`` on the next line. The gateway then refused to start with
a message about a directory existing, for a problem about a process still
holding it.

The invariant is not tidiness: prometheus-client aggregates every ``.db``
file in this directory, so one left by a dead worker is counted as though
that worker were still alive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_port_api.__main__ import _reset_multiproc_dir


def test_a_fresh_directory_is_created(tmp_path: Path) -> None:
    target = tmp_path / "prom"
    _reset_multiproc_dir(target)
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_restarting_over_an_existing_directory_is_not_an_error(
    tmp_path: Path,
) -> None:
    """The regression: the second start onwards used to fail outright."""
    target = tmp_path / "prom"
    target.mkdir()
    (target / "histogram_123.db").write_bytes(b"stale")

    _reset_multiproc_dir(target)

    assert target.is_dir()
    assert list(target.iterdir()) == [], "stale metrics were left to be counted"


def test_stale_files_from_dead_workers_do_not_survive(tmp_path: Path) -> None:
    """Every file goes, not just the ones rmtree happened to reach."""
    target = tmp_path / "prom"
    target.mkdir()
    for pid in (32884, 17140, 20528):
        (target / f"histogram_{pid}.db").write_bytes(b"stale")
        (target / f"counter_{pid}.db").write_bytes(b"stale")
    (target / "nested").mkdir()
    (target / "nested" / "deep.db").write_bytes(b"stale")

    _reset_multiproc_dir(target)

    assert list(target.iterdir()) == []


def test_a_file_that_cannot_be_removed_is_reported_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Something still holding a file means another gateway is running.

    Starting anyway would mix two instances' metrics, so this must fail --
    but with a sentence naming the problem rather than ``FileExistsError``.
    """
    target = tmp_path / "prom"
    target.mkdir()
    (target / "histogram_999.db").write_bytes(b"locked")

    # Stand in for Windows refusing to delete a memory-mapped file.
    monkeypatch.setattr(
        Path, "unlink", lambda *_a, **_k: (_ for _ in ()).throw(OSError("in use"))
    )
    monkeypatch.setattr("shutil.rmtree", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError) as excinfo:
        _reset_multiproc_dir(target)

    message = str(excinfo.value)
    assert "histogram_999.db" in message, "the operator is not told what is stuck"
    assert "still running" in message, "the likely cause is not named"
