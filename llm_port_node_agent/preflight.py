"""Startup preflight checks and static capability detection."""

from __future__ import annotations

import asyncio
import platform
import shutil
import socket
from typing import Any


async def _run(*args: str, timeout_sec: float = 8) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, "", "timeout"
    return proc.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


async def detect_gpu_count() -> int:
    """Best-effort GPU count detection using nvidia-smi."""
    if shutil.which("nvidia-smi") is None:
        return 0
    code, out, _ = await _run("nvidia-smi", "-L")
    if code != 0:
        return 0
    lines = [line for line in out.splitlines() if line.strip()]
    return len(lines)


async def docker_available() -> bool:
    """Check whether Docker CLI can talk to daemon."""
    if shutil.which("docker") is None:
        return False
    code, _, _ = await _run("docker", "version", "--format", "{{.Server.Version}}")
    return code == 0


async def build_static_capabilities() -> dict[str, Any]:
    """Return stable host capability metadata."""
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "docker_available": await docker_available(),
        "gpu_count": await detect_gpu_count(),
    }
