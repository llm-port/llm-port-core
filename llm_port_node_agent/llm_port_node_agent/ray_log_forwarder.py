"""Ship Ray Serve replica logs to Loki.

The Ray runtime container idles on ``sleep infinity``. Every Serve replica
writes to its own file under ``session_latest/logs/serve/`` inside it, so
``docker logs`` on that container is permanently empty and the only record of
what a replica did lives in those files. The deployment log panel showed
nothing at all for a deployment that was serving.

Reading them on demand works (the agent can tail them through a command) but
costs a round trip per page and cannot stream. Shipping them continuously
turns the panel into a Loki query, the same as the node's own logs, and gives
live tailing for free.

This reads ordinary files rather than shelling into the container, which is
what the session-directory bind mount in the certified bundle is for. Tailing
is ``O_RDONLY`` and cannot block Ray's writers -- Linux has no mandatory
locking. The two hazards are rotation and disk:

* **Rotation** is handled by keying cursors on ``(device, inode)`` rather than
  on the path. When Ray rotates a file the path is reused by a new inode; a
  path-keyed cursor would resume at the old offset into a new file and skip or
  duplicate lines. A file whose inode changed is read from the beginning.
* **Disk** is the bundle's problem, not this one's: ``RAY_ROTATION_MAX_BYTES``
  and ``RAY_ROTATION_BACKUP_COUNT`` are set alongside the mount, because
  raylet and worker logs do not rotate by default and a persisted session
  directory without a bound is how a node's disk fills.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from llm_port_node_agent.loki_client import LokiClient

log = logging.getLogger(__name__)

#: Where Ray keeps a session's Serve logs, inside the runtime container and --
#: once the bundle's mount is in place -- on the host.
DEFAULT_SESSION_DIR = "/var/lib/llm-port/ray"

#: ``replica_<app>_<deployment>_<replica id>.log``.
#:
#: The app name is ours (``llmport-<deployment uuid>``) and contains hyphens,
#: the deployment name is Ray's and may contain underscores, so the split is
#: anchored on the known app prefix rather than on counting underscores.
_REPLICA_FILE = re.compile(r"^replica_(?P<rest>.+)\.log$")

#: Read at most this much from one file per pass.
#:
#: A replica that logs a stack trace per request can produce megabytes between
#: passes, and a single unbounded read would hold the loop and the memory for
#: all of it. The remainder is picked up next pass: the cursor advances by
#: what was actually read.
_MAX_READ_BYTES = 1 * 1024 * 1024

#: Lines in one push. Loki rejects very large batches, and a replica that has
#: just started can emit thousands at once.
_MAX_BATCH_LINES = 500


class RayServeLogForwarder:
    """Tail Ray Serve replica log files and push them to Loki."""

    def __init__(
        self,
        *,
        loki: LokiClient,
        host: str,
        session_dir: str = DEFAULT_SESSION_DIR,
        interval_sec: int = 5,
    ) -> None:
        self._loki = loki
        self._host = host
        self._session_dir = Path(session_dir)
        self._interval = max(interval_sec, 2)
        #: ``(device, inode) -> byte offset``. Keyed on identity rather than
        #: path so a rotated file is read from the start instead of resumed
        #: at a meaningless offset.
        self._cursors: dict[tuple[int, int], int] = {}
        #: Whether a pass has completed.
        #:
        #: The distinction this draws is the difference between skipping a
        #: cluster's whole history and losing a replica's first seconds. At
        #: startup an unknown file may hold days of logs, so it is read from
        #: the end. Afterwards an unknown file is one that appeared while we
        #: were watching -- a new replica, or a rotation -- so it is read from
        #: the beginning, because it cannot have a history we missed.
        self._started = False

    async def run_forever(self) -> None:
        while True:
            try:
                await self._collect_all()
            except Exception:  # noqa: BLE001 - a log cycle never kills the agent
                log.debug("Ray Serve log collection cycle failed.", exc_info=True)
            await asyncio.sleep(self._interval)

    # ── discovery ────────────────────────────────────────────────────────

    def _serve_log_dir(self) -> Path | None:
        """The running session's ``logs/serve``, if there is one.

        ``session_latest`` is a symlink Ray maintains, and it is tempting to
        just follow it. It does not work from here: Ray writes the link with
        an **absolute path**, and that path is the one inside the container
        (``/tmp/ray/session_...``). Read through the host side of the bind
        mount it points at a directory that does not exist, so the link
        resolves to nothing and the forwarder silently found no logs at all.

        So the newest ``session_*`` directory is used instead, which is a real
        directory on both sides of the mount. The symlink is still tried
        first, because it is correct when this runs inside the container and
        it is the cheaper answer.

        A node not running Ray simply has no directory, which is not an error.
        """
        linked = self._session_dir / "session_latest" / "logs" / "serve"
        if linked.is_dir():
            return linked

        sessions = [
            path
            for path in self._session_dir.glob("session_*")
            if path.is_dir() and not path.is_symlink()
        ]
        if not sessions:
            return None
        # Newest by mtime: a node that has hosted several clusters keeps the
        # old session directories, and the live one is the one being written.
        newest = max(sessions, key=lambda path: path.stat().st_mtime)
        candidate = newest / "logs" / "serve"
        return candidate if candidate.is_dir() else None

    async def _collect_all(self) -> None:
        serve_dir = self._serve_log_dir()
        if serve_dir is None:
            return

        seen: set[tuple[int, int]] = set()
        for path in sorted(serve_dir.glob("replica_*.log")):
            try:
                key = await asyncio.to_thread(self._identity, path)
            except OSError:
                continue
            if key is None:
                continue
            seen.add(key)
            try:
                await self._tail_file(path, key)
            except Exception:  # noqa: BLE001 - one bad file is not the rest
                log.debug("Failed to tail %s", path, exc_info=True)

        # Drop cursors for files that are gone, so a long-lived agent does not
        # accumulate one entry per replica that has ever existed.
        for stale in set(self._cursors) - seen:
            self._cursors.pop(stale, None)

        self._started = True

    @staticmethod
    def _identity(path: Path) -> tuple[int, int] | None:
        stat = path.stat()
        return (stat.st_dev, stat.st_ino)

    # ── tailing ──────────────────────────────────────────────────────────

    async def _tail_file(self, path: Path, key: tuple[int, int]) -> None:
        offset = self._cursors.get(key)
        size = await asyncio.to_thread(lambda: path.stat().st_size)

        if offset is None:
            if not self._started:
                # Our first pass. This file may hold days of logs from before
                # the agent started; shipping them would be a burst of stale
                # lines into Loki all stamped with today's time. Start at the
                # end.
                self._cursors[key] = size
                return
            # It appeared while we were watching -- a replica that just
            # started, or a rotation reusing the path with a new inode. It
            # cannot have a history we missed, so read it whole. Skipping to
            # the end here would silently drop a replica's first seconds,
            # which is exactly the part worth reading when one fails to come
            # up.
            offset = 0

        if size < offset:
            # Truncated in place (``> file``, or a rotation that reused the
            # inode). Anything before the new size is gone; start over.
            offset = 0

        if size == offset:
            return

        chunk, new_offset = await asyncio.to_thread(
            self._read_from, path, offset, min(size - offset, _MAX_READ_BYTES)
        )
        self._cursors[key] = new_offset
        if not chunk:
            return

        lines = chunk.splitlines()
        if not lines:
            return

        meta = self._parse_filename(path.name)
        for batch in (
            lines[i : i + _MAX_BATCH_LINES]
            for i in range(0, len(lines), _MAX_BATCH_LINES)
        ):
            await self._push(batch, meta)

    @staticmethod
    def _read_from(path: Path, offset: int, length: int) -> tuple[str, int]:
        """Read *length* bytes from *offset*, stopping on the last full line.

        A pass can land mid-line. Shipping the fragment would split one log
        entry across two Loki entries and break its JSON; instead the read
        stops at the last newline and the cursor is left before the partial
        line, which the next pass picks up whole.
        """
        with path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(length)
        if not raw:
            return "", offset
        cut = raw.rfind(b"\n")
        if cut == -1:
            # No complete line yet -- wait rather than ship a fragment.
            return "", offset
        usable = raw[: cut + 1]
        return usable.decode("utf-8", errors="replace"), offset + len(usable)

    # ── labelling ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_filename(name: str) -> dict[str, str]:
        """Pull the app, deployment and replica out of the file name.

        Ray names these ``replica_<app>_<deployment>_<replica id>.log``. The
        app is ours and contains hyphens; the deployment is Ray's and may
        contain underscores; the replica id is the last segment. Splitting
        from the right is the only part that is reliable, so that is what
        this does -- and every field is confirmed against the JSON body when
        the line carries one.
        """
        match = _REPLICA_FILE.match(name)
        if not match:
            return {}
        rest = match.group("rest")
        parts = rest.rsplit("_", 1)
        if len(parts) != 2:
            return {"replica": rest}
        head, replica = parts
        app, _, deployment = head.partition("_")
        out = {"replica": replica}
        if app:
            out["app"] = app
        if deployment:
            out["deployment"] = deployment
        return out

    async def _push(self, lines: list[str], meta: dict[str, str]) -> None:
        """Send one batch, splitting it by level so the label is accurate.

        Loki labels belong to a stream, not to a line, so a batch containing
        both an INFO and an ERROR cannot carry one ``level``. Grouping by
        level keeps the label true; the cost is at most a handful of small
        pushes instead of one, and in practice a batch is nearly all one
        level.
        """
        by_level: dict[str, list[tuple[int, str]]] = {}
        for line in lines:
            ts_ns, level = self._read_fields(line)
            by_level.setdefault(level, []).append((ts_ns, line))

        for level, entries in by_level.items():
            labels = {
                # Deliberately its own job rather than "node-agent": these are
                # the workload's logs, and an operator reading them should not
                # have to filter the agent's out.
                "job": "ray-serve",
                "host": self._host,
                "level": level,
                **meta,
            }
            await self._loki.push_streams(labels=labels, entries=entries)

    @staticmethod
    def _read_fields(line: str) -> tuple[int, str]:
        """Timestamp and level from a JSON line, with plain-text fallbacks.

        The compiler asks Serve for JSON-encoded logs, so the common case is a
        parsed object. A line from before that setting took effect, or from a
        component that writes plain text, still has to ship -- it just carries
        the arrival time and an unknown level rather than being dropped.
        """
        now_ns = int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)
        stripped = line.lstrip()
        if not stripped.startswith("{"):
            return now_ns, "unknown"
        try:
            doc = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return now_ns, "unknown"
        if not isinstance(doc, dict):
            return now_ns, "unknown"

        level = str(doc.get("levelname") or doc.get("level") or "unknown").lower()

        raw_ts = doc.get("asctime") or doc.get("timestamp") or doc.get("time")
        ts_ns = now_ns
        if isinstance(raw_ts, (int, float)):
            # Seconds since the epoch, as Ray's JSON formatter emits.
            ts_ns = int(float(raw_ts) * 1_000_000_000)
        elif isinstance(raw_ts, str):
            try:
                # Ray's JSON formatter writes logging's default asctime:
                # "2026-09-22 09:48:00,673". The comma is a millisecond
                # separator and ``fromisoformat`` rejects it, so every line
                # silently fell back to its arrival time -- close enough to
                # look right and wrong enough to reorder a burst.
                parsed = datetime.fromisoformat(
                    raw_ts.replace(",", ".").replace("Z", "+00:00")
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                ts_ns = int(parsed.timestamp() * 1_000_000_000)
            except (ValueError, OverflowError):
                ts_ns = now_ns
        return ts_ns, level


__all__ = ["RayServeLogForwarder", "DEFAULT_SESSION_DIR"]
