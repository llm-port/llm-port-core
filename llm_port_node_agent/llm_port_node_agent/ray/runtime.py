"""Ray process/bootstrap lifecycle — the ONLY layer allowed to run the CLI.

Spec rule (section 2): bootstrap/process operations go through the Ray CLI
(``ray start --head`` / ``ray start --address`` / ``ray stop``); everything
else (status, Serve, metrics, state) goes through the Python SDK (``core.py``
et al.).

Version-parity note: the agent *packages* ``ray[serve]==2.58.0``.  The
preferred binary is the packaged CLI (``shutil.which("ray")`` resolves the
agent's own venv, or the managed venv layout under ``ray_base_path`` —
``/opt/llm-port/ray/<version>/bin/ray``).  Using the same wheel for bootstrap
*and* in-process attach guarantees SDK/cluster version parity.  When a
managed multi-version layout is present (``ray_base_path/<version>/bin/ray``),
that versioned CLI wins so the environment can pin a cluster to a specific
release; the SDK attach layer then validates the intended version
(``RayCoreClient.check_version``) and reports a mismatch as a typed error
rather than failing cluster health.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llm_port_node_agent.ray import errors

log = logging.getLogger(__name__)


def _venv_ray_binaries() -> list[Path]:
    """Candidate Ray CLI locations for the current / active Python env.

    The agent is a deployed service: its venv is usually *not* on the caller's
    PATH, so a plain ``shutil.which`` can miss the packaged CLI.  We therefore
    also look in the venv that owns ``sys.executable``.
    """
    candidates: list[Path] = []
    try:
        import sys

        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name in ("Scripts", "bin"):
            candidates.append(exe_dir / "ray.exe" if os.name == "nt" else exe_dir / "ray")
            scripts_dir = exe_dir.parent / "Scripts"
            candidates.append(scripts_dir / "ray.exe" if os.name == "nt" else scripts_dir / "ray")
        venv_bin = os.environ.get("VIRTUAL_ENV")
        if venv_bin:
            vdir = Path(venv_bin)
            candidates.append(vdir / "Scripts" / "ray.exe" if os.name == "nt" else vdir / "bin" / "ray")
        active = os.environ.get("PATH") or ""
        # PATH entries that look like a venv scripts dir get one more look.
        for entry in active.split(os.pathsep):
            ed = Path(entry)
            if ed.exists() and ed.name in ("Scripts", "bin"):
                candidates.append(ed / "ray.exe" if os.name == "nt" else ed / "ray")
    except Exception:  # pragma: no cover - defensive
        pass
    return [c for c in candidates if c]


@dataclass
class RayLastError:
    """Last bootstrap CLI failure, for diagnostics surfaces."""

    command: str
    returncode: int | None
    stderr: str
    at: str  # ISO-8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stderr": self.stderr,
            "at": self.at,
        }


class RayRuntime:
    """Drives ``ray start`` / ``ray stop`` subprocesses (bootstrap only)."""

    def __init__(self, ray_base_path: str = "/opt/llm-port/ray") -> None:
        self.ray_base_path = Path(ray_base_path)
        self.last_error: RayLastError | None = None

    # ------------------------------------------------------------------
    # binary discovery
    # ------------------------------------------------------------------

    def ray_binary_path(self, version: str) -> Path:
        """Resolve the Ray CLI for ``version``.

        Order:

        1. Managed multi-version layout (``<base>/<version>/bin/ray``) —
           explicit release pinning for a node.
        2. The *packaged* CLI: ``shutil.which("ray")`` when the venv is
           activated, otherwise the venv that the agent process itself runs in
           (``sys.executable``'s virtualenv → ``Scripts/ray.exe`` /
           ``bin/ray``).  This keeps bootstrap and attach on the same wheel.
        3. The managed path for the requested version — returned as a not-found
           marker so errors name the expected location.
        """
        managed_bin = self.ray_base_path / version / "bin" / "ray"
        if managed_bin.exists() and os.access(str(managed_bin), os.X_OK):
            return managed_bin
        system_bin = shutil.which("ray")
        if system_bin:
            return Path(system_bin)
        for env_bin in _venv_ray_binaries():
            if env_bin.exists():
                return env_bin
        return managed_bin

    async def installed_version_async(self) -> str | None:
        """Report the version of the CLI that would be used, or ``None``.

        Parses ``ray, version X.Y.Z`` from ``ray --version`` (best-effort;
        a missing binary yields ``None``).
        """
        ray_bin = self.ray_binary_path("")
        if not ray_bin.exists():
            return None
        try:
            proc = await self._run([str(ray_bin), "--version"])
        except Exception:
            return None
        out = (proc.stdout or b"").decode(errors="replace")
        for line in out.splitlines():
            parts = line.split(",")
            if (
                len(parts) >= 2
                and parts[0].strip().lower() == "ray"
                and parts[1].startswith(" version")
            ):
                return parts[1].strip().split(None, 1)[-1]
        return None

    # ------------------------------------------------------------------
    # bootstrap operations (the ONLY three approved CLI commands)
    # ------------------------------------------------------------------

    async def start_head(
        self,
        *,
        version: str,
        port: int,
        dashboard_port: int,
        dashboard_host: str,
        node_ip_address: str | None = None,
        num_cpus: int | None = None,
        num_gpus: int | None = None,
        include_dashboard: bool = True,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """``ray start --head`` with token-auth env injected by the caller."""
        args = [
            "start",
            "--head",
            f"--port={port}",
        ]
        if node_ip_address:
            args.append(f"--node-ip-address={node_ip_address}")
        if include_dashboard:
            args += [f"--dashboard-host={dashboard_host}", f"--dashboard-port={dashboard_port}"]
        else:
            args.append("--include-dashboard=false")
        if num_cpus is not None:
            args.append(f"--num-cpus={num_cpus}")
        if num_gpus is not None:
            args.append(f"--num-gpus={num_gpus}")

        proc = await self._exec(
            args,
            version=version,
            env=env,
            label="ray start --head",
            tolerate_already_running=True,
        )
        resolved_host = node_ip_address or dashboard_host
        dash_url_host = resolved_host if dashboard_host in ("0.0.0.0", "127.0.0.1") and node_ip_address else dashboard_host
        return {
            "started": True,
            "head_address": f"{resolved_host}:{port}",
            "cluster_address": f"{resolved_host}:{port}",
            "dashboard_url": f"http://{dash_url_host}:{dashboard_port}" if include_dashboard else None,
        }

    async def join_cluster(
        self,
        *,
        version: str,
        head_address: str,
        node_ip_address: str | None = None,
        num_cpus: int | None = None,
        num_gpus: int | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """``ray start --address=<head>`` to join this node as a worker."""
        args = ["start", f"--address={head_address}"]
        if node_ip_address:
            args.append(f"--node-ip-address={node_ip_address}")
        if num_cpus is not None:
            args.append(f"--num-cpus={num_cpus}")
        if num_gpus is not None:
            args.append(f"--num-gpus={num_gpus}")

        await self._exec(
            args,
            version=version,
            env=env,
            label="ray start --address",
            tolerate_already_running=True,
        )
        return {"joined": True, "head_address": head_address}

    async def stop(self, *, version: str, force: bool = False) -> dict[str, Any]:
        """``ray stop [--force]`` — tear down local node processes only.

        Never raises: stopping into a clean state when nothing is running is
        the goal; failures are recorded in :attr:`last_error` and reported
        via ``best_effort`` so an idempotent leave/stop stays idempotent.
        """
        args = ["stop"]
        if force:
            args.append("--force")
        try:
            await self._exec(args, version=version, env=None, label="ray stop",
                             tolerate_missing_processes=True)
            return {"stopped": True}
        except errors.RayRuntimeError as exc:
            return {"stopped": False, "best_effort": True, "detail": str(exc)}

    # ------------------------------------------------------------------
    # subprocess plumbing
    # ------------------------------------------------------------------

    async def _run(self, args: list[str], env: dict[str, str] | None = None) -> asyncio.subprocess.Process:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        process = await asyncio.create_subprocess_exec(
            *args,
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        proc = process
        proc.stdout = stdout  # type: ignore[attr-defined]
        proc.stderr = stderr  # type: ignore[attr-defined]
        return proc

    async def _exec(
        self,
        args: list[str],
        *,
        version: str,
        env: dict[str, str] | None,
        label: str,
        tolerate_missing_processes: bool = False,
        tolerate_already_running: bool = False,
    ) -> asyncio.subprocess.Process:
        ray_bin = self.ray_binary_path(version)
        if not ray_bin.exists():
            self.last_error = RayLastError(
                command=label,
                returncode=None,
                stderr=f"Ray binary not found at {ray_bin}",
                at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            raise errors.RayRuntimeError(
                f"Ray binary not found for version {version!r} at {ray_bin}",
                detail=str(ray_bin),
            )

        full_cmd = [str(ray_bin), *args]
        log.info("Ray bootstrap: %s", " ".join(full_cmd))
        try:
            proc = await self._run(full_cmd, env=env)
        except FileNotFoundError as exc:
            self.last_error = RayLastError(
                command=label, returncode=None, stderr=str(exc),
                at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            raise errors.RayRuntimeError(f"{label} failed to launch: {exc}") from exc

        stdout_text = (proc.stdout or b"").decode(errors="replace").strip()
        stderr_text = (proc.stderr or b"").decode(errors="replace").strip()
        if proc.returncode != 0:
            detail = stderr_text or stdout_text
            if tolerate_missing_processes and _means_nothing_running(detail):
                log.info("%s reported no local processes (idempotent no-op)", label)
                return proc
            if tolerate_already_running and _means_already_running(detail):
                log.info("%s reported node already running/joined (idempotent no-op)", label)
                return proc
            self.last_error = RayLastError(
                command=label,
                returncode=proc.returncode,
                stderr=detail,
                at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            raise errors.RayRuntimeError(
                f"{label} failed (exit {proc.returncode}): {detail}",
                detail=detail,
            )
        if self.last_error is not None and label != "ray stop":
            self.last_error = None
        return proc


def _means_nothing_running(detail: str) -> bool:
    d = detail.lower()
    return (
        "no local processes to stop" in d
        or "no ray processes" in d
        or "nothing to stop" in d
        or "not running" in d
        or "not found" in d
    )


def _means_already_running(detail: str) -> bool:
    d = detail.lower()
    return (
        "already part of a ray cluster" in d
        or "already running" in d
        or "already started" in d
        or "address is already in use" in d
        or "already joined" in d
        or "connection to the ray cluster already exists" in d
    )
