"""System log collection for Linux (journald) and Windows (Event Log).

Yields timestamped log lines from the host OS, resuming from
the last-seen cursor on each call.  The collector is designed to
be called periodically from an async loop — heavy I/O runs in a
thread executor so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass(slots=True)
class LogEntry:
    """Single log line with nanosecond-precision timestamp."""

    timestamp_ns: int
    line: str
    level: str = "info"


class LogCollector:
    """Cross-platform system log reader.

    On Linux, reads from ``journalctl --output=short-iso`` (no extra
    dependencies).  On Windows, reads the System event log through
    ``wevtutil``.  Both approaches use cursor-based pagination so that
    only new entries are returned on each call.
    """

    def __init__(self, *, max_lines: int = 500) -> None:
        self._max_lines = max_lines
        # journalctl cursor for resuming
        self._journal_cursor: str | None = None
        # Windows: last timestamp we saw (ISO string)
        self._win_last_ts: str | None = None

    async def collect(self) -> list[LogEntry]:
        """Return new log entries since last call."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._collect_sync)

    def _collect_sync(self) -> list[LogEntry]:
        if sys.platform == "win32":
            return self._collect_windows()
        return self._collect_journald()

    # ── Linux: journalctl ────────────────────────────────────

    def _collect_journald(self) -> list[LogEntry]:
        import subprocess

        cmd = [
            "journalctl",
            "--no-pager",
            "--output=short-iso",
            f"--lines={self._max_lines}",
        ]
        if self._journal_cursor:
            cmd.extend(["--after-cursor", self._journal_cursor])
        else:
            # First run: only grab last N lines
            pass

        # Ask journalctl to show the cursor for each entry
        cmd.append("--show-cursor")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            log.debug("journalctl not found — system log collection disabled.")
            return []
        except subprocess.TimeoutExpired:
            log.warning("journalctl timed out.")
            return []

        if result.returncode != 0:
            log.debug("journalctl exited %d: %s", result.returncode, result.stderr[:200])
            return []

        entries: list[LogEntry] = []
        last_cursor: str | None = None

        for raw_line in result.stdout.splitlines():
            # Cursor lines look like: "-- cursor: s=abc123..."
            if raw_line.startswith("-- cursor: "):
                last_cursor = raw_line[len("-- cursor: "):].strip()
                continue
            if not raw_line.strip():
                continue
            ts_ns, level = self._parse_journal_line_meta(raw_line)
            entries.append(LogEntry(timestamp_ns=ts_ns, line=raw_line, level=level))

        if last_cursor:
            self._journal_cursor = last_cursor

        return entries

    @staticmethod
    def _parse_journal_line_meta(line: str) -> tuple[int, str]:
        """Extract timestamp and severity from a journalctl short-iso line.

        Format: ``2026-03-21T14:05:32+0000 hostname unit[pid]: message``
        """
        level = "info"
        ts_ns = time.time_ns()  # fallback

        # Try to parse the ISO timestamp at the start
        parts = line.split(" ", 1)
        if parts:
            try:
                dt = datetime.fromisoformat(parts[0])
                ts_ns = int(dt.timestamp() * 1_000_000_000)
            except ValueError:
                pass

        # Heuristic level detection from message content
        lower = line.lower()
        if "error" in lower or "fatal" in lower or "crit" in lower:
            level = "error"
        elif "warn" in lower:
            level = "warning"
        elif "debug" in lower:
            level = "debug"

        return ts_ns, level

    # ── Windows: wevtutil ────────────────────────────────────

    def _collect_windows(self) -> list[LogEntry]:
        import subprocess
        import xml.etree.ElementTree as ET

        # Build XPath query for System log
        if self._win_last_ts:
            # Only events after last seen timestamp
            xpath = (
                f"*[System[TimeCreated[@SystemTime>'{self._win_last_ts}']]]"
            )
            cmd = [
                "wevtutil", "qe", "System",
                f"/q:{xpath}",
                f"/c:{self._max_lines}",
                "/f:xml",
                "/rd:true",
            ]
        else:
            cmd = [
                "wevtutil", "qe", "System",
                f"/c:{self._max_lines}",
                "/f:xml",
                "/rd:true",
            ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError:
            log.debug("wevtutil not found — system log collection disabled.")
            return []
        except subprocess.TimeoutExpired:
            log.warning("wevtutil timed out.")
            return []

        if result.returncode != 0:
            log.debug("wevtutil exited %d: %s", result.returncode, result.stderr[:200])
            return []

        entries: list[LogEntry] = []
        max_ts: str | None = None

        # wevtutil outputs multiple <Event> elements without a root
        wrapped = f"<Events>{result.stdout}</Events>"
        try:
            root = ET.fromstring(wrapped)
        except ET.ParseError:
            log.debug("Failed to parse wevtutil XML output.")
            return []

        ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

        for event in root.findall(".//e:Event", ns):
            system = event.find("e:System", ns)
            if system is None:
                continue

            # Timestamp
            tc = system.find("e:TimeCreated", ns)
            ts_str = tc.get("SystemTime", "") if tc is not None else ""
            if ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts_ns = int(dt.timestamp() * 1_000_000_000)
                except ValueError:
                    ts_ns = time.time_ns()
                if max_ts is None or ts_str > max_ts:
                    max_ts = ts_str
            else:
                ts_ns = time.time_ns()

            # Level
            level_el = system.find("e:Level", ns)
            level = _win_level(int(level_el.text) if level_el is not None and level_el.text else 4)

            # Provider + EventID for the log line
            provider_el = system.find("e:Provider", ns)
            provider = provider_el.get("Name", "System") if provider_el is not None else "System"
            eid_el = system.find("e:EventID", ns)
            eid = eid_el.text if eid_el is not None and eid_el.text else "0"

            # Message data
            event_data = event.find("e:EventData", ns)
            msg_parts: list[str] = []
            if event_data is not None:
                for data in event_data.findall("e:Data", ns):
                    if data.text:
                        msg_parts.append(data.text)
            message = " ".join(msg_parts) if msg_parts else f"EventID={eid}"

            line = f"{ts_str} {provider}[{eid}]: {message}"
            entries.append(LogEntry(timestamp_ns=ts_ns, line=line, level=level))

        if max_ts:
            self._win_last_ts = max_ts

        # Reverse so entries are chronological (wevtutil /rd:true = newest first)
        entries.reverse()
        return entries


def _win_level(level_id: int) -> str:
    """Map Windows event level integer to string."""
    return {
        1: "error",    # Critical
        2: "error",    # Error
        3: "warning",  # Warning
        4: "info",     # Information
        5: "debug",    # Verbose
    }.get(level_id, "info")
