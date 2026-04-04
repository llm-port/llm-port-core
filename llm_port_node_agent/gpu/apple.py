"""Apple Metal GPU collector using system_profiler."""

from __future__ import annotations

import asyncio
import json
import logging

import psutil

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


class AppleMetalCollector:
    """Collect GPU info on macOS via ``system_profiler SPDisplaysDataType``."""

    async def snapshot(self) -> GpuSnapshot:
        code, out, _ = await _run(
            "system_profiler", "SPDisplaysDataType", "-json",
        )
        if code != 0:
            return GpuSnapshot()

        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return GpuSnapshot()

        displays = data.get("SPDisplaysDataType") or []
        devices: list[GpuDevice] = []

        for gpu in displays:
            name = gpu.get("sppci_model", "Apple GPU")
            # Apple Silicon uses unified memory — report system RAM as GPU memory
            vram_str = gpu.get("spdisplays_vram") or gpu.get("spdisplays_vram_shared") or ""
            vram_mib = self._parse_vram_string(vram_str)

            if vram_mib == 0:
                # Unified memory: use total system RAM
                vm = psutil.virtual_memory()
                vram_mib = vm.total // (1024 * 1024)

            devices.append(
                GpuDevice(
                    memory_total_mib=vram_mib,
                    memory_used_mib=0,  # not directly available
                    utilization_pct=None,
                    temperature_c=None,
                    vendor="apple",
                    name=name,
                ),
            )

        if not devices:
            return GpuSnapshot()

        total_vram = sum(d.memory_total_mib for d in devices) * 1024 * 1024
        return GpuSnapshot(
            count=len(devices),
            devices=devices,
            total_vram_bytes=total_vram,
            used_vram_bytes=0,
            free_vram_bytes=total_vram,
        )

    async def device_count(self) -> int:
        snap = await self.snapshot()
        return snap.count

    @property
    def vendor(self) -> str:
        return "apple"

    @staticmethod
    def _parse_vram_string(vram_str: str) -> int:
        """Parse system_profiler VRAM strings like '16 GB' into MiB."""
        if not vram_str:
            return 0
        vram_str = vram_str.strip()
        parts = vram_str.split()
        if len(parts) < 2:
            try:
                return int(parts[0])
            except ValueError:
                return 0
        try:
            value = int(parts[0])
        except ValueError:
            return 0
        unit = parts[1].upper()
        if unit in {"GB", "GO"}:
            return value * 1024
        if unit in {"MB", "MO"}:
            return value
        if unit in {"TB", "TO"}:
            return value * 1024 * 1024
        return value
