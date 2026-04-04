"""Startup preflight checks and static capability detection."""

from __future__ import annotations

import platform
import socket
from typing import Any

from llm_port_node_agent.gpu import GpuCollector, NullCollector
from llm_port_node_agent.runtimes import ContainerRuntime


async def docker_available(runtime: ContainerRuntime | None = None) -> bool:
    """Check whether the container runtime is reachable.

    If *runtime* is provided, delegates to ``runtime.is_available()``.
    Otherwise returns False.
    """
    if runtime is not None:
        return await runtime.is_available()
    return False


async def build_static_capabilities(
    runtime: ContainerRuntime | None = None,
    gpu_collector: GpuCollector | None = None,
) -> dict[str, Any]:
    """Return stable host capability metadata."""
    gpu = gpu_collector or NullCollector()
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "docker_available": await docker_available(runtime),
        "gpu_count": await gpu.device_count(),
        "gpu_vendor": gpu.vendor,
        "container_runtime": runtime.name if runtime else "unknown",
    }
