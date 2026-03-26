"""AMD ROCm GPU collector stub using rocm-smi."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil

from llm_port_node_agent.gpu import GpuDevice, GpuSnapshot

log = logging.getLogger(__name__)


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


class RocmCollector:
    """Collect GPU info via ``rocm-smi`` (AMD GPUs)."""

    async def snapshot(self) -> GpuSnapshot:
        if shutil.which("rocm-smi") is None:
            return GpuSnapshot()

        code, out, _ = await _run(
            "rocm-smi", "--showmeminfo", "vram", "--json",
        )
        if code != 0:
            return GpuSnapshot()

        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return GpuSnapshot()

        devices: list[GpuDevice] = []
        total_mib = 0
        used_mib = 0

        for key, card in data.items():
            if not isinstance(card, dict):
                continue
            # rocm-smi JSON uses bytes for VRAM fields
            vram_total = int(card.get("VRAM Total Memory (B)", 0))
            vram_used = int(card.get("VRAM Total Used Memory (B)", 0))
            t_mib = vram_total // (1024 * 1024)
            u_mib = vram_used // (1024 * 1024)
            total_mib += t_mib
            used_mib += u_mib
            devices.append(
                GpuDevice(
                    memory_total_mib=t_mib,
                    memory_used_mib=u_mib,
                    vendor="amd",
                    name=key,
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
        snap = await self.snapshot()
        return snap.count

    @property
    def vendor(self) -> str:
        return "amd"
