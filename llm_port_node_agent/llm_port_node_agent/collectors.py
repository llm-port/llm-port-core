"""Host inventory and utilization collectors."""

from __future__ import annotations

import os
import sys
from typing import Any

import psutil

from llm_port_node_agent.gpu import GpuCollector, GpuSnapshot, NullCollector

# Module-level default used when no collector is injected.
_default_collector: GpuCollector = NullCollector()


def _disk_root() -> str:
    """Return the root filesystem path for the current platform."""
    if sys.platform == "win32":
        return os.environ.get("SystemDrive", "C:") + os.sep
    return "/"


async def collect_gpu_snapshot(
    collector: GpuCollector | None = None,
) -> dict[str, Any]:
    """Collect GPU snapshot via the injected collector."""
    gpu = collector or _default_collector
    snap: GpuSnapshot = await gpu.snapshot()
    return snap.to_dict()


async def collect_inventory(
    static_capabilities: dict[str, Any],
    *,
    gpu_snapshot: dict[str, Any] | None = None,
    collector: GpuCollector | None = None,
) -> dict[str, Any]:
    """Collect mostly-static hardware inventory."""
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(_disk_root())
    gpu = gpu_snapshot if gpu_snapshot is not None else await collect_gpu_snapshot(collector)
    return {
        "cpu_count_logical": psutil.cpu_count(logical=True) or 0,
        "cpu_count_physical": psutil.cpu_count(logical=False) or 0,
        "memory_total_bytes": vm.total,
        "disk_total_bytes": du.total,
        "network_interfaces": list(psutil.net_if_addrs().keys()),
        "gpu_count": int(gpu.get("count", 0)),
        "gpu": gpu,
        "static_capabilities": static_capabilities,
    }


async def collect_utilization(
    *,
    gpu_snapshot: dict[str, Any] | None = None,
    collector: GpuCollector | None = None,
) -> dict[str, Any]:
    """Collect changing utilization metrics."""
    cpu = psutil.cpu_percent(interval=None)
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(_disk_root())
    net = psutil.net_io_counters()
    gpu = gpu_snapshot if gpu_snapshot is not None else await collect_gpu_snapshot(collector)
    return {
        "cpu_percent": cpu,
        "memory_used_bytes": vm.used,
        "memory_available_bytes": vm.available,
        "memory_percent": vm.percent,
        "disk_used_bytes": du.used,
        "disk_free_bytes": du.free,
        "disk_percent": du.percent,
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
        },
        "gpu_free_vram_bytes": int(gpu.get("free_vram_bytes", 0)),
        "gpu": gpu,
    }
