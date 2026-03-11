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

# Base images used in multi-stage Dockerfiles that BuildKit manages
# internally (not visible in ``docker images``).  Pre-pulling them
# puts them in the local store so Docker Desktop shows them and
# subsequent builds hit the cache without a network round-trip.
_BASE_IMAGES = [
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
    pull: bool = False,
) -> int:
    """Run ``docker compose up``."""
    cmd = ctx.base_cmd() + ["up"]
    if detach:
        cmd.append("-d")
    if build:
        cmd.append("--build")
    if pull:
        cmd.append("--pull=always")
    if services:
        cmd.extend(services)
    result = _run(cmd, stream=True)
    return result.returncode


def down(ctx: ComposeContext, *, volumes: bool = False, remove_orphans: bool = False) -> int:
    """Run ``docker compose down``."""
    cmd = ctx.base_cmd() + ["down"]
    if volumes:
        cmd.append("-v")
    if remove_orphans:
        cmd.append("--remove-orphans")
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


def build_context_from_config(cfg: "LlmportConfig") -> ComposeContext:
    """Build a ComposeContext from the current config."""
    files = [cfg.compose_path]
    return ComposeContext(
        compose_files=files,
        env_file=cfg.env_path if cfg.env_path.exists() else None,
        project_dir=cfg.install_path,
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
