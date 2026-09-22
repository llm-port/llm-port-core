"""Shipping Ray Serve replica logs to Loki.

The runtime container idles on ``sleep infinity`` and every replica writes to
its own file inside it, so the container's console is permanently empty. The
deployment log panel showed nothing at all for a deployment that was serving,
and reading the files on demand costs a node round trip per page and cannot
stream.

The mechanism is a tail, and tails go wrong in specific ways. What is pinned
here is the handling of those, because each one loses or duplicates log lines
silently:

  * a file rotated under us, where the path is reused by a new inode;
  * a read landing mid-line;
  * a file truncated in place;
  * a first sighting, where shipping the whole history would flood Loki with
    stale lines stamped with today's time.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_port_node_agent.ray_log_forwarder import RayServeLogForwarder

pytestmark = pytest.mark.anyio()

APP = "llmport-b64e389b-58ae-4b1d-a754-1f9f48e22220"
REPLICA_FILE = f"replica_{APP}_LLMServer_Qwen2_5-0_5B-Instruct_wib8cvxx.log"


class _Loki:
    """Records pushes instead of sending them."""

    def __init__(self) -> None:
        self.pushes: list[tuple[dict[str, str], list[tuple[int, str]]]] = []

    async def push_streams(self, *, labels: dict[str, str], entries: list) -> bool:
        self.pushes.append((dict(labels), list(entries)))
        return True

    @property
    def lines(self) -> list[str]:
        return [text for _, entries in self.pushes for _, text in entries]


@pytest.fixture()
def serve_dir(tmp_path: Path) -> Path:
    """``session_latest/logs/serve``, as Ray lays it out."""
    path = tmp_path / "session_latest" / "logs" / "serve"
    path.mkdir(parents=True)
    return path


def _forwarder(tmp_path: Path, loki: _Loki) -> RayServeLogForwarder:
    return RayServeLogForwarder(loki=loki, host="10.88.10.71", session_dir=str(tmp_path))


def _entry(message: str, level: str = "INFO") -> str:
    return json.dumps(
        {
            "asctime": "2026-09-22T09:00:00Z",
            "levelname": level,
            "message": message,
            "deployment": "LLMServer",
        }
    )


# ── the ordinary path ────────────────────────────────────────────────────


async def test_new_lines_are_shipped(serve_dir: Path, tmp_path: Path) -> None:
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text(_entry("first") + "\n", encoding="utf-8")

    # First pass establishes the cursor at the end.
    await forwarder._collect_all()
    assert loki.lines == []

    log_file.write_text(
        _entry("first") + "\n" + _entry("second") + "\n", encoding="utf-8"
    )
    await forwarder._collect_all()

    assert any("second" in line for line in loki.lines)


async def test_a_first_sighting_does_not_ship_the_whole_history(
    serve_dir: Path, tmp_path: Path
) -> None:
    """A cluster up for days would otherwise flood Loki on every agent restart.

    Worse than the volume: those lines would arrive stamped with the moment
    the agent came back, so a week of history would land as a spike of
    "now".
    """
    loki = _Loki()
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text("".join(_entry(f"old {i}") + "\n" for i in range(500)), encoding="utf-8")

    await _forwarder(tmp_path, loki)._collect_all()

    assert loki.pushes == []


async def test_lines_are_not_shipped_twice(serve_dir: Path, tmp_path: Path) -> None:
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text(_entry("a") + "\n", encoding="utf-8")
    await forwarder._collect_all()

    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(_entry("b") + "\n")
    await forwarder._collect_all()
    await forwarder._collect_all()

    assert sum("\"b\"" in line or "b" == json.loads(line)["message"] for line in loki.lines) == 1


# ── the ways a tail goes wrong ───────────────────────────────────────────


async def test_a_read_landing_mid_line_waits_for_the_rest(
    serve_dir: Path, tmp_path: Path
) -> None:
    """Shipping a fragment splits one entry across two, and breaks its JSON.

    The cursor must stay before the partial line so the next pass picks it up
    whole.
    """
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text(_entry("complete") + "\n", encoding="utf-8")
    await forwarder._collect_all()

    # A line still being written: no trailing newline yet.
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write('{"message": "half')
    await forwarder._collect_all()
    assert loki.lines == [], "a partial line was shipped"

    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(' written"}\n')
    await forwarder._collect_all()

    assert len(loki.lines) == 1
    assert json.loads(loki.lines[0])["message"] == "half written"


async def test_a_rotated_file_is_read_from_the_start(
    serve_dir: Path, tmp_path: Path
) -> None:
    """Ray reuses the path with a new inode.

    A cursor keyed on the path would resume at the old offset into a new
    file, skipping its beginning or reading past its end -- and would do so
    silently.
    """
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text("".join(_entry(f"old {i}") + "\n" for i in range(50)), encoding="utf-8")
    await forwarder._collect_all()

    # Rotate: the old file moves aside and a fresh, much smaller one appears.
    log_file.rename(serve_dir / (REPLICA_FILE + ".1"))
    log_file.write_text(_entry("after rotation") + "\n", encoding="utf-8")

    await forwarder._collect_all()

    assert any("after rotation" in line for line in loki.lines)


async def test_a_file_truncated_in_place_is_re_read(
    serve_dir: Path, tmp_path: Path
) -> None:
    """``> file`` keeps the inode and resets the size.

    Resuming at the old offset would read nothing, forever.
    """
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text("".join(_entry(f"x {i}") + "\n" for i in range(50)), encoding="utf-8")
    await forwarder._collect_all()

    log_file.write_text(_entry("after truncation") + "\n", encoding="utf-8")
    await forwarder._collect_all()

    assert any("after truncation" in line for line in loki.lines)


async def test_a_replica_that_goes_away_does_not_leak_a_cursor(
    serve_dir: Path, tmp_path: Path
) -> None:
    """A long-lived agent would otherwise hold one entry per replica ever seen."""
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text(_entry("hello") + "\n", encoding="utf-8")
    await forwarder._collect_all()
    assert len(forwarder._cursors) == 1

    log_file.unlink()
    await forwarder._collect_all()

    assert forwarder._cursors == {}


# ── labels ───────────────────────────────────────────────────────────────


async def test_the_stream_is_labelled_for_the_deployment_panel(
    serve_dir: Path, tmp_path: Path
) -> None:
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text(_entry("a") + "\n", encoding="utf-8")
    await forwarder._collect_all()
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(_entry("b") + "\n")
    await forwarder._collect_all()

    labels, _ = loki.pushes[0]
    # Its own job: an operator reading a workload's logs should not have to
    # filter the agent's out.
    assert labels["job"] == "ray-serve"
    assert labels["host"] == "10.88.10.71"
    assert labels["app"] == APP
    assert labels["deployment"] == "LLMServer_Qwen2_5-0_5B-Instruct"
    assert labels["replica"] == "wib8cvxx"


async def test_a_batch_of_mixed_levels_does_not_mislabel_any_of_them(
    serve_dir: Path, tmp_path: Path
) -> None:
    """A Loki label belongs to a stream, not to a line.

    One push carrying both an INFO and an ERROR would have to pick one
    ``level`` for both, and whichever it picked would be wrong about the
    other -- so an error would be invisible to ``{level="error"}``.
    """
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text(_entry("a") + "\n", encoding="utf-8")
    await forwarder._collect_all()

    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(_entry("fine", "INFO") + "\n")
        handle.write(_entry("broken", "ERROR") + "\n")
    await forwarder._collect_all()

    by_level = {labels["level"]: entries for labels, entries in loki.pushes}
    assert set(by_level) == {"info", "error"}
    assert "broken" in by_level["error"][0][1]


async def test_a_plain_text_line_still_ships(serve_dir: Path, tmp_path: Path) -> None:
    """Logs from before JSON encoding took effect, or from another component.

    Dropping them would mean the panel silently omits exactly the lines
    written while something was going wrong with the configuration.
    """
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text(_entry("a") + "\n", encoding="utf-8")
    await forwarder._collect_all()

    with log_file.open("a", encoding="utf-8") as handle:
        handle.write("INFO 2026-09-22 plain old line\n")
    await forwarder._collect_all()

    assert any("plain old line" in line for line in loki.lines)
    labels, _ = loki.pushes[0]
    assert labels["level"] == "unknown"


async def test_the_json_timestamp_is_used_not_the_arrival_time(
    serve_dir: Path, tmp_path: Path
) -> None:
    """Otherwise a burst of lines all land at the moment they were read."""
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text(_entry("a") + "\n", encoding="utf-8")
    await forwarder._collect_all()

    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(_entry("b") + "\n")
    await forwarder._collect_all()

    (_, entries), = [(l, e) for l, e in loki.pushes if e]
    ts_ns, _ = entries[0]
    expected = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
    assert ts_ns == int(expected.timestamp() * 1_000_000_000)


# ── nothing to do ────────────────────────────────────────────────────────


async def test_a_node_not_running_ray_is_not_an_error(tmp_path: Path) -> None:
    loki = _Loki()
    await _forwarder(tmp_path, loki)._collect_all()
    assert loki.pushes == []


async def test_files_that_are_not_replica_logs_are_ignored(
    serve_dir: Path, tmp_path: Path
) -> None:
    """The proxy's log lives in the same directory and is a different stream."""
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    (serve_dir / "proxy_10.100.0.1.log").write_text("proxy line\n", encoding="utf-8")
    await forwarder._collect_all()
    assert forwarder._cursors == {}


# ── finding the session at all ───────────────────────────────────────────


async def test_the_session_is_found_without_the_symlink(tmp_path: Path) -> None:
    """``session_latest`` cannot be followed from the host side of the mount.

    Ray writes that link with an absolute path, and the path it records is
    the one inside the container (``/tmp/ray/session_...``). Read through the
    host's side of the bind mount it points at a directory that does not
    exist, so the link resolves to nothing. Found on hardware: the mount was
    correct, the files were there, and the forwarder shipped nothing.
    """
    session = tmp_path / "session_2026-09-22_09-36-02_847722_88"
    serve = session / "logs" / "serve"
    serve.mkdir(parents=True)
    (serve / REPLICA_FILE).write_text(_entry("a") + "\n", encoding="utf-8")

    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    assert forwarder._serve_log_dir() == serve


async def test_a_dangling_session_latest_does_not_hide_the_real_one(
    tmp_path: Path,
) -> None:
    """Exactly the hardware case: a broken link beside a real directory."""
    session = tmp_path / "session_2026-09-22_09-36-02_847722_88"
    serve = session / "logs" / "serve"
    serve.mkdir(parents=True)
    link = tmp_path / "session_latest"
    try:
        link.symlink_to("/tmp/ray/session_that_does_not_exist", target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - needs privilege
        pytest.skip("this platform will not create the symlink")

    forwarder = _forwarder(tmp_path, _Loki())
    assert forwarder._serve_log_dir() == serve


async def test_the_newest_session_wins(tmp_path: Path) -> None:
    """A node that has hosted several clusters keeps the old directories."""
    import os
    import time

    older = tmp_path / "session_2026-09-01_00-00-00_1_1" / "logs" / "serve"
    newer = tmp_path / "session_2026-09-22_09-36-02_2_2" / "logs" / "serve"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    past = time.time() - 86_400
    os.utime(older.parent.parent, (past, past))

    forwarder = _forwarder(tmp_path, _Loki())
    assert forwarder._serve_log_dir() == newer


async def test_rays_own_timestamp_format_is_understood(
    serve_dir: Path, tmp_path: Path
) -> None:
    """Ray writes logging's default ``asctime``: a space and a comma.

    ``2026-09-22 09:48:00,673``. ``fromisoformat`` rejects the comma, so every
    line fell back to its arrival time -- close enough to look right on a
    quiet cluster, and wrong enough to reorder a burst.
    """
    loki = _Loki()
    forwarder = _forwarder(tmp_path, loki)
    log_file = serve_dir / REPLICA_FILE
    log_file.write_text("{}\n", encoding="utf-8")
    await forwarder._collect_all()

    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "asctime": "2026-09-22 09:48:00,673",
                    "levelname": "INFO",
                    "message": "Finished initializing replica.",
                }
            )
            + "\n"
        )
    await forwarder._collect_all()

    (_, entries), = [(l, e) for l, e in loki.pushes if e]
    ts_ns, _ = entries[0]
    expected = datetime(2026, 9, 22, 9, 48, 0, 673_000, tzinfo=UTC)
    assert ts_ns == int(expected.timestamp() * 1_000_000_000)
