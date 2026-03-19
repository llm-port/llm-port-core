"""Host inventory and utilization collectors."""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

import psutil


async def _run(*args: str, timeout_sec: float = 6) -> tuple[int, str, str]:
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


async def collect_gpu_snapshot() -> dict[str, Any]:
    """Collect basic NVIDIA GPU usage when available."""
    if shutil.which("nvidia-smi") is None:
        return {"count": 0, "free_vram_bytes": 0}
    query = (
        "--query-gpu=memory.total,memory.used,utilization.gpu,temperature.gpu"
    )
    code, out, _ = await _run("nvidia-smi", query, "--format=csv,noheader,nounits")
    if code != 0:
        return {"count": 0, "free_vram_bytes": 0}

    total_mib = 0
    used_mib = 0
    rows = []
    for row in out.splitlines():
        parts = [chunk.strip() for chunk in row.split(",")]
        if len(parts) < 4:
            continue
        try:
            total = int(parts[0])
            used = int(parts[1])
            util = int(parts[2])
            temp = int(parts[3])
        except ValueError:
            continue
        total_mib += total
        used_mib += used
        rows.append(
            {
                "memory_total_mib": total,
                "memory_used_mib": used,
                "utilization_pct": util,
                "temperature_c": temp,
            },
        )

    return {
        "count": len(rows),
        "devices": rows,
        "total_vram_bytes": total_mib * 1024 * 1024,
        "used_vram_bytes": used_mib * 1024 * 1024,
        "free_vram_bytes": max(total_mib - used_mib, 0) * 1024 * 1024,
    }


async def collect_inventory(static_capabilities: dict[str, Any]) -> dict[str, Any]:
    """Collect mostly-static hardware inventory."""
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    gpu = await collect_gpu_snapshot()
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


async def collect_utilization() -> dict[str, Any]:
    """Collect changing utilization metrics."""
    cpu = psutil.cpu_percent(interval=None)
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    gpu = await collect_gpu_snapshot()
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
