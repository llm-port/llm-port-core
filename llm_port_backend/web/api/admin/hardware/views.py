"""Admin hardware detection endpoints.

``GET /admin/hardware`` — returns GPU inventory, live metrics,
the recommended vLLM image tag, and a list of available image presets
(built-in + admin-defined from ``LLM_PORT_BACKEND_VLLM_IMAGE_PRESETS``).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from llm_port_backend.db.models.users import User
from llm_port_backend.services.gpu.detector import detect_gpus
from llm_port_backend.services.gpu.metrics import collect_gpu_metrics
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.dependencies import require_superuser
from llm_port_backend.web.api.admin.hardware.schema import (
    GpuDeviceDTO,
    GpuInventoryDTO,
    GpuMetricsDTO,
    HardwareDTO,
    VllmImagePresetDTO,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Built-in image presets ────────────────────────────────────────────
# Always available; extra presets are loaded from the env-var setting.

_BUILTIN_PRESETS: list[dict[str, Any]] = [
    {
        "label": "vLLM (CUDA)",
        "image": settings.default_vllm_image,
        "vendor": "nvidia",
        "description": "Default Docker Hub image for NVIDIA GPUs (CUDA).",
        "is_default": True,
    },
    {
        "label": "vLLM (ROCm)",
        "image": settings.default_vllm_rocm_image,
        "vendor": "amd",
        "description": "Default Docker Hub image for AMD GPUs (ROCm).",
        "is_default": True,
    },
]

# Vendor string → recommended image tag (used for backwards-compat field)
_VLLM_IMAGES: dict[str, str] = {
    "nvidia": settings.default_vllm_image,
    "amd": settings.default_vllm_rocm_image,
}


def _load_custom_presets() -> list[dict[str, Any]]:
    """Parse ``settings.vllm_image_presets`` JSON into dicts.

    Silently returns an empty list on invalid JSON so the endpoint
    never breaks because of a misconfigured env var.
    """
    raw = settings.vllm_image_presets
    if not raw or raw.strip() == "[]":
        return []
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            logger.warning("vllm_image_presets is not a JSON array — ignoring.")
            return []
        return [
            {
                "label": p.get("label", p.get("image", "?")),
                "image": p["image"],
                "vendor": p.get("vendor"),
                "description": p.get("description"),
                "is_default": False,
            }
            for p in items
            if isinstance(p, dict) and "image" in p
        ]
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse vllm_image_presets JSON — ignoring.", exc_info=True)
        return []


def _build_presets(primary_vendor: str) -> list[VllmImagePresetDTO]:
    """Merge built-in and custom presets, mark the recommended one."""
    all_raw = _BUILTIN_PRESETS + _load_custom_presets()
    presets: list[VllmImagePresetDTO] = []
    recommended_set = False

    for raw in all_raw:
        # A preset is recommended when its vendor matches the detected GPU
        is_rec = False
        preset_vendor = raw.get("vendor")
        if not recommended_set and preset_vendor and preset_vendor == primary_vendor:
            is_rec = True
            recommended_set = True

        presets.append(
            VllmImagePresetDTO(
                label=raw["label"],
                image=raw["image"],
                vendor=preset_vendor,
                description=raw.get("description"),
                is_default=raw.get("is_default", False),
                is_recommended=is_rec,
            ),
        )

    return presets


@router.get(
    "",
    response_model=HardwareDTO,
    name="admin_hardware_info",
    summary="Detect host GPU hardware and live metrics",
)
async def hardware_info(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> HardwareDTO:
    """Return detected GPU inventory, live utilisation metrics, the
    recommended vLLM image tag for the primary GPU vendor, and a list
    of available image presets (built-in + custom).
    """

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
    presets = _build_presets(inventory.primary_vendor.value)

    logger.info(
        "Hardware probe: vendor=%s GPUs=%d VRAM=%s recommended=%s presets=%d",
        inventory.primary_vendor.value,
        inventory.device_count,
        inventory.total_vram_bytes,
        recommended_image,
        len(presets),
    )

    return HardwareDTO(
        gpu=gpu_dto,
        gpu_metrics=metrics_dto,
        recommended_vllm_image=recommended_image,
        vllm_image_presets=presets,
    )
