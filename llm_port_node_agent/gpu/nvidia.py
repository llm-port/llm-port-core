"""NVIDIA GPU collector using nvidia-smi CLI."""

from __future__ import annotations

import asyncio
import shutil

from llm_port_node_agent.gpu import GpuDevice, GpuSnapshot


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


class NvidiaCollector:
    """Collect GPU metrics via ``nvidia-smi``."""

    async def snapshot(self) -> GpuSnapshot:
        if shutil.which("nvidia-smi") is None:
            return GpuSnapshot()

        query = "--query-gpu=memory.total,memory.used,utilization.gpu,temperature.gpu"
        code, out, _ = await _run("nvidia-smi", query, "--format=csv,noheader,nounits")
        if code != 0:
            return GpuSnapshot()

        total_mib = 0
        used_mib = 0
        devices: list[GpuDevice] = []
        for row in out.splitlines():
            parts = [chunk.strip() for chunk in row.split(",")]
            if len(parts) < 4:
                continue
            try:
                total = int(parts[0])
            except ValueError:
                total = 0
            try:
                used = int(parts[1])
            except ValueError:
                used = 0
            try:
                util: int | None = int(parts[2])
            except ValueError:
                util = None
            try:
                temp: int | None = int(parts[3])
            except ValueError:
                temp = None
            if total == 0 and used == 0 and util is None and temp is None:
                continue
            total_mib += total
            used_mib += used
            devices.append(
                GpuDevice(
                    memory_total_mib=total,
                    memory_used_mib=used,
                    utilization_pct=util,
                    temperature_c=temp,
                    vendor="nvidia",
                ),
            )

        return GpuSnapshot(
            count=len(devices),
            devices=devices,
            total_vram_bytes=total_mib * 1024 * 1024,
            used_vram_bytes=used_mib * 1024 * 1024,
            free_vram_bytes=max(total_mib - used_mib, 0) * 1024 * 1024,
        )

    async def device_count(self) -> int:
        if shutil.which("nvidia-smi") is None:
            return 0
        code, out, _ = await _run("nvidia-smi", "-L")
        if code != 0:
            return 0
        return len([line for line in out.splitlines() if line.strip()])

    @property
    def vendor(self) -> str:
        return "nvidia"
