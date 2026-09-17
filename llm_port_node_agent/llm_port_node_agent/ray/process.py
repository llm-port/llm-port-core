"""OS-level subprocess management for Ray binaries."""

import asyncio
import logging
import os
import signal
from collections.abc import AsyncGenerator
from pathlib import Path

log = logging.getLogger(__name__)


class RayProcessManager:
    """Manages Ray CLI subprocess execution."""

    def __init__(self, ray_base_path: str = "/opt/llm-port/ray") -> None:
        self.ray_base_path = Path(ray_base_path)

    def ray_binary_path(self, version: str) -> Path:
        """Resolve the path to the Ray CLI binary for a given version."""
        # Check managed venv first
        managed_bin = self.ray_base_path / version / "bin" / "ray"
        if managed_bin.exists() and os.access(managed_bin, os.X_OK):
            return managed_bin
        # Fall back to system path if not found in managed venv
        import shutil

        system_bin = shutil.which("ray")
        if system_bin:
            return Path(system_bin)
        return managed_bin  # return expected path even if missing for error clarity

    async def start(
        self,
        args: list[str],
        version: str,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        """Launch a Ray CLI command."""
        ray_bin = self.ray_binary_path(version)
        if not ray_bin.exists():
            raise FileNotFoundError(f"Ray binary not found at {ray_bin}")

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        log.info("Starting Ray process: %s %s", ray_bin, " ".join(args))
        process = await asyncio.create_subprocess_exec(
            str(ray_bin),
            *args,
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # We don't await process.wait() here because `ray start` daemonizes.
        # But we capture the immediate output to check for startup errors.
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            err = stderr.decode().strip() or stdout.decode().strip()
            raise RuntimeError(f"Ray command failed (exit {process.returncode}): {err}")

        return process

    async def stop(self, version: str, force: bool = False) -> None:
        """Stop Ray processes on this node."""
        args = ["stop"]
        if force:
            args.append("--force")

        try:
            await self.start(args, version=version)
            log.info("Successfully stopped Ray processes.")
        except Exception as e:
            log.warning("Error stopping Ray processes: %s", e)

    async def is_running(self, version: str) -> bool:
        """Check if Ray processes are running."""
        # Simple heuristic: run `ray status`
        try:
            ray_bin = self.ray_binary_path(version)
            if not ray_bin.exists():
                return False

            process = await asyncio.create_subprocess_exec(
                str(ray_bin),
                "status",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()
            return process.returncode == 0
        except Exception:
            return False

