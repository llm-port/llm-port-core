"""Vendor-neutral GPU data types."""

from __future__ import annotations

import dataclasses
import enum


class GpuVendor(enum.StrEnum):
    """Known GPU vendors supported (or planned) by the platform."""

    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    APPLE = "apple"
    UNKNOWN = "unknown"


class GpuComputeApi(enum.StrEnum):
    """Low-level compute API / driver stack used for inference."""

    CUDA = "cuda"
    ROCM = "rocm"
    ONEAPI = "oneapi"
    METAL = "metal"
    VULKAN = "vulkan"  # llama.cpp can use this on any GPU
    CPU = "cpu"
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class GpuDevice:
    """Describes a single GPU device detected on the host.

    Attributes:
        index:          Ordinal device index (0, 1, …).
        vendor:         GPU vendor enum.
        model:          Human-readable model name, e.g. "NVIDIA A100 80GB".
        vram_bytes:     Total VRAM in bytes (0 if unknown).
        driver_version: Driver version string, e.g. "535.183.01" or "6.2.4".
        compute_api:    Primary compute API available.
    """

    index: int
    vendor: GpuVendor
    model: str
    vram_bytes: int = 0
    driver_version: str = ""
    compute_api: GpuComputeApi = GpuComputeApi.UNKNOWN


@dataclasses.dataclass(frozen=True)
class GpuMetrics:
    """Point-in-time GPU metrics for a single device or aggregated across all."""

    util_percent: float | None = None
    vram_used_bytes: int | None = None
    vram_total_bytes: int | None = None


@dataclasses.dataclass(frozen=True)
class GpuInventory:
    """Complete GPU inventory for the host."""

    devices: list[GpuDevice] = dataclasses.field(default_factory=list)
    primary_vendor: GpuVendor = GpuVendor.UNKNOWN
    primary_compute_api: GpuComputeApi = GpuComputeApi.UNKNOWN

    @property
    def has_gpu(self) -> bool:
        return len(self.devices) > 0

    @property
    def device_count(self) -> int:
        return len(self.devices)

    @property
    def total_vram_bytes(self) -> int:
        return sum(d.vram_bytes for d in self.devices)
