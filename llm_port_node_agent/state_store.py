"""Persistent local state for credentials, offsets, and workloads."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AgentState:
    """Serializable state snapshot persisted to disk."""

    node_id: str | None = None
    credential: str | None = None
    tx_seq: int = 0
    maintenance_mode: bool = False
    draining: bool = False
    workloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed_commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())


class StateStore:
    """File-backed state storage with atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state = AgentState()
        self.load()

    def load(self) -> None:
        """Load local state when file exists."""
        if not self.path.exists():
            self.save()
            return
        with self.path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        self.state = AgentState(
            node_id=raw.get("node_id"),
            credential=raw.get("credential"),
            tx_seq=int(raw.get("tx_seq") or 0),
            maintenance_mode=bool(raw.get("maintenance_mode", False)),
            draining=bool(raw.get("draining", False)),
            workloads=dict(raw.get("workloads") or {}),
            completed_commands=dict(raw.get("completed_commands") or {}),
            updated_at=str(raw.get("updated_at") or datetime.now(tz=UTC).isoformat()),
        )

    def save(self) -> None:
        """Write state atomically to avoid truncated files."""
        self.state.updated_at = datetime.now(tz=UTC).isoformat()
        payload = {
            "node_id": self.state.node_id,
            "credential": self.state.credential,
            "tx_seq": self.state.tx_seq,
            "maintenance_mode": self.state.maintenance_mode,
            "draining": self.state.draining,
            "workloads": self.state.workloads,
            "completed_commands": self.state.completed_commands,
            "updated_at": self.state.updated_at,
        }
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        tmp.replace(self.path)

    def next_seq(self) -> int:
        """Return next monotonic tx sequence for stream messages."""
        self.state.tx_seq += 1
        self.save()
        return self.state.tx_seq

    def remember_command_result(self, command_id: str, payload: dict[str, Any]) -> None:
        """Persist completed command result for idempotent replay."""
        self.state.completed_commands[command_id] = payload
        # keep bounded memory on disk
        max_entries = 500
        if len(self.state.completed_commands) > max_entries:
            keys = list(self.state.completed_commands.keys())
            for key in keys[: len(keys) - max_entries]:
                self.state.completed_commands.pop(key, None)
        self.save()

    def get_command_result(self, command_id: str) -> dict[str, Any] | None:
        """Read cached completed result for a command."""
        row = self.state.completed_commands.get(command_id)
        if not isinstance(row, dict):
            return None
        return dict(row)

    def set_workload(self, runtime_id: str, payload: dict[str, Any]) -> None:
        """Upsert workload state."""
        self.state.workloads[runtime_id] = payload
        self.save()

    def drop_workload(self, runtime_id: str) -> None:
        """Remove workload state."""
        self.state.workloads.pop(runtime_id, None)
        self.save()

    def workload(self, runtime_id: str) -> dict[str, Any] | None:
        """Get workload state by runtime id."""
        row = self.state.workloads.get(runtime_id)
        if not isinstance(row, dict):
            return None
        return dict(row)
