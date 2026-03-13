"""Docker Compose subprocess wrapper.

Wraps ``docker compose`` (v2) as a thin Python layer.  Every function
shells out rather than using a Docker SDK so the CLI has zero runtime
dependency on Docker libraries and works on any system that has the
``docker`` binary.

Environment and profile arguments are built from the
:class:`~llmport.core.settings.LlmportConfig` dataclass so callers
never need to assemble raw CLI flags.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from llmport.core.console import console, error


# ── Types ─────────────────────────────────────────────────────────


@dataclass
class ComposeService:
    """Parsed row from ``docker compose ps --format json``."""

    name: str = ""
    service: str = ""
    status: str = ""  # running, exited, created, …
    health: str = ""  # healthy, unhealthy, starting, ""
    ports: str = ""
    state: str = ""
    exit_code: int = 0


@dataclass
class ComposeContext:
    """Everything needed to run a compose command."""

    compose_files: list[Path] = field(default_factory=list)
    env_file: Path | None = None
    project_dir: Path | None = None
    profiles: list[str] = field(default_factory=list)

    def base_cmd(self) -> list[str]:
        """Build the ``docker compose`` prefix with all file/profile flags."""
        docker = shutil.which("docker")
        if not docker:
            error("docker not found on PATH")
            sys.exit(1)

        cmd: list[str] = [docker, "compose"]
        for f in self.compose_files:
            cmd.extend(["-f", str(f)])
        if self.env_file and Path(str(self.env_file)).exists():
            cmd.extend(["--env-file", str(self.env_file)])
        if self.project_dir:
            cmd.extend(["--project-directory", str(self.project_dir)])
        for p in self.profiles:
            cmd.extend(["--profile", p])
        return cmd


# ── Constants ─────────────────────────────────────────────────────

# Base images used in the platform base Dockerfile.  Pre-pulling
# puts them in the local store so subsequent builds hit cache.
_BASE_IMAGES = [
    "python:3.13-slim-bookworm",
    "ghcr.io/astral-sh/uv:0.9.12",
]


# ── Public API ────────────────────────────────────────────────────


def pull_base_images() -> None:
    """Pre-pull shared base images so BuildKit finds them locally."""
    docker = shutil.which("docker")
    if not docker:
        return
    for image in _BASE_IMAGES:
        subprocess.run(  # noqa: S603
            [docker, "pull", image],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def build_base_image(ctx: ComposeContext, *, no_cache: bool = False) -> int:
    """Build the shared platform base image via compose.

    Uses the ``llm-port-base`` service (gated behind the ``_build``
    profile) so the image lands in the same BuildKit context that
    subsequent service builds use.
    """
    base_ctx = ComposeContext(
        compose_files=ctx.compose_files,
        env_file=ctx.env_file,
        project_dir=ctx.project_dir,
        profiles=["_build"],
    )
    return build(base_ctx, services=["llm-port-base"], no_cache=no_cache)


def _run(
    cmd: list[str],
    *,
    capture: bool = False,
    check: bool = True,
    stream: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute a command, optionally streaming output to the console."""
    if stream:
        # Stream stdout/stderr to terminal in real time
        result = subprocess.run(cmd, check=False)  # noqa: S603
        if check and result.returncode != 0:
            error(f"Command failed with exit code {result.returncode}")
        return subprocess.CompletedProcess(cmd, result.returncode, "", "")

    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=capture,
        text=True,
        check=False,
    )


def up(
    ctx: ComposeContext,
    *,
    services: list[str] | None = None,
    detach: bool = True,
    build: bool = False,
    pull: str = "",
    wait: bool = False,
    timeout: int = 0,
    force_recreate: bool = False,
) -> int:
    """Run ``docker compose up``.

    *pull* accepts ``"always"``, ``"missing"``, or ``"never"``.
    An empty string (default) leaves Docker Compose to decide.
    *wait* blocks until services are healthy (implies ``-d``).
    *timeout* sets the max wait time in seconds (requires *wait*).
    *force_recreate* recreates containers even if config hasn't changed.
    """
    cmd = ctx.base_cmd() + ["up"]
    if detach:
        cmd.append("-d")
    if build:
        cmd.append("--build")
    if pull:
        cmd.append(f"--pull={pull}")
    if force_recreate:
        cmd.append("--force-recreate")
    if wait:
        cmd.append("--wait")
    if timeout > 0:
        cmd.append(f"--wait-timeout={timeout}")
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def down(
    ctx: ComposeContext,
    *,
    volumes: bool = False,
    remove_orphans: bool = False,
    rmi: str | None = None,
) -> int:
    """Run ``docker compose down``.

    Args:
        rmi: Remove images. ``"all"`` removes all images, ``"local"``
             removes only images without a custom tag.
    """
    cmd = ctx.base_cmd() + ["down"]
    if volumes:
        cmd.append("-v")
    if remove_orphans:
        cmd.append("--remove-orphans")
    if rmi:
        cmd.extend(["--rmi", rmi])
    result = _run(cmd, stream=True)
    return result.returncode


def restart(ctx: ComposeContext, *, services: list[str] | None = None) -> int:
    """Run ``docker compose restart`` for the given services."""
    cmd = ctx.base_cmd() + ["restart"]
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def pull(ctx: ComposeContext, *, services: list[str] | None = None) -> int:
    """Run ``docker compose pull``."""
    cmd = ctx.base_cmd() + ["pull"]
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def build(
    ctx: ComposeContext,
    *,
    services: list[str] | None = None,
    no_cache: bool = False,
    pull: bool = False,
) -> int:
    """Run ``docker compose build``."""
    cmd = ctx.base_cmd() + ["build"]
    if no_cache:
        cmd.append("--no-cache")
    if pull:
        cmd.append("--pull")
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def logs(
    ctx: ComposeContext,
    *,
    services: list[str] | None = None,
    follow: bool = True,
    tail: int | None = 100,
    timestamps: bool = False,
) -> int:
    """Run ``docker compose logs``."""
    cmd = ctx.base_cmd() + ["logs"]
    if follow:
        cmd.append("-f")
    if tail is not None:
        cmd.extend(["--tail", str(tail)])
    if timestamps:
        cmd.append("--timestamps")
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def ps(ctx: ComposeContext) -> list[ComposeService]:
    """Run ``docker compose ps`` and parse the output."""
    import json as _json

    cmd = ctx.base_cmd() + ["ps", "--format", "json", "-a"]
    result = _run(cmd, capture=True)
    if result.returncode != 0:
        return []

    services: list[ComposeService] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
            svc = ComposeService(
                name=obj.get("Name", ""),
                service=obj.get("Service", ""),
                status=obj.get("State", obj.get("Status", "")),
                health=obj.get("Health", ""),
                ports=obj.get("Publishers", obj.get("Ports", "")),
                state=obj.get("State", ""),
                exit_code=obj.get("ExitCode", 0),
            )
            # Format ports if it's a list of dicts
            if isinstance(svc.ports, list):
                port_strs = []
                for p in svc.ports:
                    if isinstance(p, dict) and p.get("PublishedPort"):
                        port_strs.append(f"{p.get('URL', '0.0.0.0')}:{p['PublishedPort']}→{p['TargetPort']}")
                svc.ports = ", ".join(port_strs) if port_strs else ""
            services.append(svc)
        except (ValueError, KeyError):
            continue
    return services


def exec_cmd(
    ctx: ComposeContext,
    service: str,
    command: list[str],
    *,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose exec`` on a running service."""
    cmd = ctx.base_cmd() + ["exec", service, *command]
    return _run(cmd, capture=capture)


def run_cmd(
    ctx: ComposeContext,
    service: str,
    command: list[str],
    *,
    capture: bool = False,
    remove: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose run`` (one-off container)."""
    cmd = ctx.base_cmd() + ["run"]
    if remove:
        cmd.append("--rm")
    cmd.extend([service, *command])
    return _run(cmd, capture=capture)


def _resolve_compose_path(cfg: "LlmportConfig") -> Path:
    """Resolve the compose file, falling back to llm_port_shared/."""
    primary = cfg.compose_path
    if primary.exists():
        return primary
    # Check inside llm_port_shared relative to install path
    for name in ("docker-compose.yaml", "docker-compose.yml"):
        candidate = cfg.install_path / "llm_port_shared" / name
        if candidate.exists():
            return candidate
    return primary


def build_context_from_config(cfg: "LlmportConfig") -> ComposeContext:
    """Build a ComposeContext from the current config."""
    compose_file = _resolve_compose_path(cfg)
    files = [compose_file]
    return ComposeContext(
        compose_files=files,
        env_file=cfg.env_path if cfg.env_path.exists() else None,
        project_dir=compose_file.parent,
        profiles=list(cfg.profiles),
    )


def build_context_from_paths(
    compose_file: Path,
    *,
    env_file: Path | None = None,
    project_dir: Path | None = None,
    profiles: list[str] | None = None,
    dev_overlay: Path | None = None,
) -> ComposeContext:
    """Build a ComposeContext from explicit paths."""
    files = [compose_file]
    if dev_overlay and dev_overlay.exists():
        files.append(dev_overlay)
    return ComposeContext(
        compose_files=files,
        env_file=env_file,
        project_dir=project_dir or compose_file.parent,
        profiles=profiles or [],
    )
