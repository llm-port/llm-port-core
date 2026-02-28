"""Schemas for admin hardware detection endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GpuDeviceDTO(BaseModel):
    """Single GPU device info."""

    index: int
    vendor: str
    model: str
    vram_bytes: int
    driver_version: str
    compute_api: str


class GpuInventoryDTO(BaseModel):
    """Detected GPU hardware inventory."""

    devices: list[GpuDeviceDTO] = Field(default_factory=list)
    primary_vendor: str = "unknown"
    primary_compute_api: str = "unknown"
    has_gpu: bool = False
    device_count: int = 0
    total_vram_bytes: int = 0


class GpuMetricsDTO(BaseModel):
    """Current GPU utilization snapshot."""

    util_percent: float | None = None
    vram_used_bytes: int | None = None
    vram_total_bytes: int | None = None


class HardwareDTO(BaseModel):
    """Combined hardware inventory + live metrics."""

    gpu: GpuInventoryDTO
    gpu_metrics: GpuMetricsDTO
    recommended_vllm_image: str | None = None
