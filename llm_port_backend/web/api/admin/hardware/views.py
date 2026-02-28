"""Admin hardware detection endpoints.

``GET /admin/hardware`` — returns GPU inventory, live metrics,
and the recommended vLLM image tag for the detected GPU vendor.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from llm_port_backend.db.models.users import User
from llm_port_backend.services.gpu.detector import detect_gpus
from llm_port_backend.services.gpu.metrics import collect_gpu_metrics
from llm_port_backend.web.api.admin.dependencies import require_superuser
from llm_port_backend.web.api.admin.hardware.schema import (
    GpuDeviceDTO,
    GpuInventoryDTO,
    GpuMetricsDTO,
    HardwareDTO,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Image recommendations per vendor (mirrors the mapping in vllm adapter)
_VLLM_IMAGES: dict[str, str] = {
    "nvidia": "vllm/vllm-openai:latest",
    "amd": "vllm/vllm-openai:latest-rocm",
}


@router.get(
    "",
    response_model=HardwareDTO,
    name="admin_hardware_info",
    summary="Detect host GPU hardware and live metrics",
)
async def hardware_info(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> HardwareDTO:
    """Return detected GPU inventory, live utilisation metrics, and the
    recommended vLLM image tag for the primary GPU vendor."""

    inventory = detect_gpus()
    metrics = collect_gpu_metrics()

    gpu_devices = [
        GpuDeviceDTO(
            index=d.index,
            vendor=d.vendor.value,
            model=d.model,
            vram_bytes=d.vram_bytes,
            driver_version=d.driver_version,
            compute_api=d.compute_api.value,
        )
        for d in inventory.devices
    ]

    gpu_dto = GpuInventoryDTO(
        devices=gpu_devices,
        primary_vendor=inventory.primary_vendor.value,
        primary_compute_api=inventory.primary_compute_api.value,
        has_gpu=inventory.has_gpu,
        device_count=inventory.device_count,
        total_vram_bytes=inventory.total_vram_bytes,
    )

    metrics_dto = GpuMetricsDTO(
        util_percent=metrics.util_percent,
        vram_used_bytes=metrics.vram_used_bytes,
        vram_total_bytes=metrics.vram_total_bytes,
    )

    recommended_image = _VLLM_IMAGES.get(inventory.primary_vendor.value)

    logger.info(
        "Hardware probe: vendor=%s GPUs=%d VRAM=%s recommended=%s",
        inventory.primary_vendor.value,
        inventory.device_count,
        inventory.total_vram_bytes,
        recommended_image,
    )

    return HardwareDTO(
        gpu=gpu_dto,
        gpu_metrics=metrics_dto,
        recommended_vllm_image=recommended_image,
    )
