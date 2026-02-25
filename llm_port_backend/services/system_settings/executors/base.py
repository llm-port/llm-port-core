"""Executor contracts for system apply operations."""

from __future__ import annotations

from dataclasses import dataclass

from llm_port_backend.db.models.system_settings import SystemApplyScope


@dataclass(frozen=True)
class ApplyAction:
    """One concrete action generated from changed settings."""

    scope: SystemApplyScope
    services: tuple[str, ...]
    changed_keys: tuple[str, ...]


class ApplyExecutor:
    """Abstract executor for apply operations."""

    async def execute(self, action: ApplyAction, target_host: str) -> list[str]:
        """Execute action and return user-facing event messages."""
        msg = "Executor.execute must be implemented."
        raise NotImplementedError(msg)
