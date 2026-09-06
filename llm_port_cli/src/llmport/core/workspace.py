"""Shared workspace helpers for ``llmport dev`` commands.

``dev init`` clones the llm-port repos as a *monorepo* at
``<workspace>/llm-port-core/<local_name>``.  Consumers (``dev up``,
``dev status``, …) must resolve service paths the same way, which is
why the helpers live here instead of being duplicated per command.
"""

from __future__ import annotations

from pathlib import Path

CORE_DIRNAME = "llm-port-core"

# Well-known llm-port dev processes and the log files they write to in
# headless (no terminal-emulator) mode.
LOG_LABELS = {
    "backend": "llmport-backend.log",
    "worker": "llmport-taskiq-worker.log",
    "frontend": "llmport-frontend.log",
}


def find_service_dir(workspace: Path, name: str) -> Path:
    """Return the path to a service directory, checking the monorepo first.

    ``llmport dev init`` clones everything under
    ``<workspace>/llm-port-core/<name>``.  Legacy/flat layouts that keep
    the service directly in ``<workspace>/<name>`` still work.
    """
    monorepo = workspace / CORE_DIRNAME / name
    if monorepo.is_dir():
        return monorepo
    return workspace / name


def resolve_shared_compose(workspace: Path) -> Path | None:
    """Locate the shared docker-compose file for a dev workspace."""
    for candidate in (
        workspace / "llm_port_shared" / "docker-compose.yaml",
        workspace / "llm_port_shared" / "docker-compose.yml",
        workspace / CORE_DIRNAME / "llm_port_shared" / "docker-compose.yaml",
        workspace / CORE_DIRNAME / "llm_port_shared" / "docker-compose.yml",
        workspace / "infra" / "shared" / "docker-compose.yaml",
        workspace / "infra" / "shared" / "docker-compose.yml",
    ):
        if candidate.exists():
            return candidate
    return None


def log_filename(label: str) -> str:
    """Log file name for a dev process label (``backend``/``worker``/``frontend``)."""
    return LOG_LABELS.get(label, f"llmport-{label}.log")


def dev_logs_dir() -> Path:
    """Directory holding background (headless) dev process log files."""
    return Path.home() / ".llmport-dev" / "logs"
