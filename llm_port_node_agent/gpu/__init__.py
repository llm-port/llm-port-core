"""GPU collector abstraction layer.

Defines the ``GpuCollector`` protocol that all GPU backends
(NVIDIA, Apple Metal, AMD ROCm) must implement, plus a
``detect_gpu()`` factory that probes the host and returns the
appropriate collector.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True, frozen=True)
class GpuDevice:
    """Single GPU device snapshot."""

    memory_total_mib: int = 0
    memory_used_mib: int = 0
    utilization_pct: int | None = None
    temperature_c: int | None = None
    vendor: str = "unknown"
    name: str = ""


@dataclass(slots=True)
class GpuSnapshot:
    """Aggregated GPU state across all devices."""

    count: int = 0
    devices: list[GpuDevice] = field(default_factory=list)
    total_vram_bytes: int = 0
    used_vram_bytes: int = 0
    free_vram_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the dict format expected by collectors/inventory."""
        return {
            "count": self.count,
            "devices": [
                {
                    "memory_total_mib": d.memory_total_mib,
                    "memory_used_mib": d.memory_used_mib,
                    "utilization_pct": d.utilization_pct,
                    "temperature_c": d.temperature_c,
                    "vendor": d.vendor,
                    "name": d.name,
                }
                for d in self.devices
            ],
            "total_vram_bytes": self.total_vram_bytes,
            "used_vram_bytes": self.used_vram_bytes,
            "free_vram_bytes": self.free_vram_bytes,
        }


_EMPTY_SNAPSHOT = GpuSnapshot()


@runtime_checkable
class GpuCollector(Protocol):
    """Async interface every GPU collector must satisfy."""

    async def snapshot(self) -> GpuSnapshot:
        """Collect current GPU state."""
        ...

    async def device_count(self) -> int:
        """Return the number of detected GPU devices."""
        ...

    @property
    def vendor(self) -> str:
        """GPU vendor identifier, e.g. ``'nvidia'``, ``'apple'``, ``'amd'``."""
        ...


class NullCollector:
    """No-op collector used when no GPU is detected."""

    async def snapshot(self) -> GpuSnapshot:
        return _EMPTY_SNAPSHOT

    async def device_count(self) -> int:
        return 0

    @property
    def vendor(self) -> str:
        return "none"


# ── factory ───────────────────────────────────────────────────

def detect_gpu(*, preferred: str | None = None) -> GpuCollector:
    """Auto-detect the appropriate GPU collector for this host.

    Parameters
    ----------
    preferred:
        If set, force a specific vendor (``"nvidia"``, ``"apple"``,
        ``"amd"``).  Falls back to auto-detection if the preferred
        tooling is absent.
    """
    import shutil

    if preferred:
        preferred = preferred.strip().lower()

    # Try preferred first, then auto-detect in priority order
    candidates: list[str] = []
    if preferred and preferred != "auto":
        candidates.append(preferred)
    candidates.extend(["nvidia", "apple", "amd"])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    for vendor in unique:
        if vendor == "nvidia" and shutil.which("nvidia-smi") is not None:
            from llm_port_node_agent.gpu.nvidia import NvidiaCollector
            return NvidiaCollector()
        if vendor == "apple" and sys.platform == "darwin":
            from llm_port_node_agent.gpu.apple import AppleMetalCollector
            return AppleMetalCollector()
        if vendor == "amd" and shutil.which("rocm-smi") is not None:
            from llm_port_node_agent.gpu.amd import RocmCollector
            return RocmCollector()

    return NullCollector()
