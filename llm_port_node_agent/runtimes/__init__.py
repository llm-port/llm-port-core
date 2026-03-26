"""Container runtime abstraction layer.

Defines the ``ContainerRuntime`` protocol that all runtime backends
(Docker, Podman, …) must implement, and a ``detect_runtime()`` factory
that probes the host and returns the first working backend.
"""

from __future__ import annotations

import shutil
from typing import Any, Protocol, runtime_checkable


class ContainerRuntimeError(RuntimeError):
    """Raised when a container-runtime operation fails."""


@runtime_checkable
class ContainerRuntime(Protocol):
    """Async interface every container runtime must satisfy."""

    # ── lifecycle ─────────────────────────────────────────────

    async def run(
        self,
        *,
        image: str,
        name: str,
        ports: list[str] | None = None,
        env: dict[str, str] | None = None,
        gpus: str | None = None,
        volumes: list[str] | None = None,
        command: list[str] | None = None,
        extra_args: list[str] | None = None,
        timeout_sec: float = 120,
    ) -> str:
        """Create and start a container.  Returns container id."""
        ...

    async def start(self, name: str, *, timeout_sec: float = 30) -> None: ...
    async def stop(self, name: str, *, timeout_sec: float = 30) -> None: ...
    async def restart(self, name: str, *, timeout_sec: float = 45) -> None: ...
    async def remove(self, name: str, *, force: bool = True, timeout_sec: float = 45) -> None: ...

    # ── query ─────────────────────────────────────────────────

    async def inspect(self, name: str, *, format_: str | None = None, timeout_sec: float = 10) -> dict[str, Any]:
        """Return parsed JSON from ``inspect``.  *format_* is passed as ``--format``."""
        ...

    async def exists(self, name: str) -> bool: ...

    async def port(self, name: str, container_port: str, *, timeout_sec: float = 10) -> str | None:
        """Return the host port mapped to *container_port*, or ``None``."""
        ...

    async def logs(
        self,
        name: str,
        *,
        tail: str | None = None,
        since: str | None = None,
        timestamps: bool = False,
        timeout_sec: float = 15,
    ) -> tuple[int, str]:
        """Fetch container logs.  Returns ``(returncode, combined_output)``."""
        ...

    async def ps(self, *, all_: bool = True, timeout_sec: float = 20) -> list[str]:
        """Return raw JSON lines from ``ps``."""
        ...

    async def images(self, *, timeout_sec: float = 20) -> list[str]:
        """Return raw JSON lines from ``images``."""
        ...

    # ── image management ──────────────────────────────────────

    async def pull(self, image: str, *, timeout_sec: float = 1800) -> None: ...

    async def load_image_tar(
        self,
        stream: Any,
        *,
        timeout_sec: float = 3600,
    ) -> str:
        """Pipe a tar stream into ``<runtime> load``.  Returns stdout."""
        ...

    # ── availability ──────────────────────────────────────────

    async def is_available(self) -> bool:
        """Return True if the runtime CLI can talk to its daemon."""
        ...

    @property
    def name(self) -> str:
        """Human-readable runtime name, e.g. ``'docker'`` or ``'podman'``."""
        ...


# ── factory ───────────────────────────────────────────────────

def detect_runtime(*, preferred: str | None = None) -> ContainerRuntime:
    """Auto-detect the first usable container runtime on the host.

    Parameters
    ----------
    preferred:
        If set, try this runtime first (``"docker"`` or ``"podman"``).
        Falls back to auto-detection if the preferred CLI is absent.
    """
    from llm_port_node_agent.runtimes.docker import DockerRuntime  # noqa: E402 — late import avoids cycles
    from llm_port_node_agent.runtimes.podman import PodmanRuntime  # noqa: E402

    candidates: list[tuple[str, type[ContainerRuntime]]] = [
        ("docker", DockerRuntime),
        ("podman", PodmanRuntime),
    ]

    if preferred:
        preferred = preferred.strip().lower()
        if preferred != "auto":
            candidates.sort(key=lambda c: c[0] != preferred)

    for cli_name, cls in candidates:
        if shutil.which(cli_name) is not None:
            return cls()  # type: ignore[abstract]

    # Nothing found — return DockerRuntime anyway; is_available() will report False.
    return DockerRuntime()  # type: ignore[abstract]
