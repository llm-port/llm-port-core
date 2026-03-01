"""Vendor-aware Docker GPU passthrough configuration.

Translates the abstract ``gpu_devices`` + ``GpuVendor`` into the correct
Docker Engine API host-config fields:

* **NVIDIA** → ``DeviceRequests`` with ``Driver: nvidia`` (requires the
  NVIDIA Container Toolkit).
* **AMD ROCm** → raw device mounts (``/dev/kfd``, ``/dev/dri/*``),
  supplementary groups ``video`` + ``render``, and
  ``seccomp=unconfined``.
* **Intel oneAPI** → raw device mounts (``/dev/dri/*``).
* **Apple Metal** → not Docker-based; raises an error so callers know
  they need a native execution path.
* **CPU / UNKNOWN** → no GPU passthrough at all.
"""

from __future__ import annotations

import logging
from typing import Any

from llm_port_backend.services.gpu.types import GpuVendor

logger = logging.getLogger(__name__)


def build_gpu_host_config(
    vendor: GpuVendor,
    gpu_devices: str | list[int] | None = None,
) -> dict[str, Any]:
    """Return Docker ``HostConfig`` fragment for GPU passthrough.

    The returned dict should be **merged** into the existing
    ``HostConfig`` when creating a container.

    :param vendor: The GPU vendor to target.
    :param gpu_devices: ``"all"`` to expose every GPU, or a list of
        integer indices (e.g. ``[0, 1]``).  ``None`` means no GPU.
    :returns: A dict with keys such as ``DeviceRequests``, ``Devices``,
        ``GroupAdd``, ``SecurityOpt`` — whatever the vendor requires.
    :raises NotImplementedError: For Apple Metal (no Docker support).
    """
    if gpu_devices is None:
        return {}

    if vendor == GpuVendor.NVIDIA:
        return _nvidia_config(gpu_devices)
    if vendor == GpuVendor.AMD:
        return _amd_config(gpu_devices)
    if vendor == GpuVendor.INTEL:
        return _intel_config(gpu_devices)
    if vendor == GpuVendor.APPLE:
        raise NotImplementedError(
            "Apple Metal GPUs cannot be passed through to Docker containers. "
            "Use a native execution mode (e.g. Ollama running directly on the "
            "host) for Apple Silicon inference."
        )

    # UNKNOWN / CPU — try NVIDIA style as best-effort fallback
    logger.warning(
        "Unknown GPU vendor %r; attempting NVIDIA-style DeviceRequests",
        vendor,
    )
    return _nvidia_config(gpu_devices)


# ── Vendor-specific builders ─────────────────────────────────────────


def _nvidia_config(gpu_devices: str | list[int]) -> dict[str, Any]:
    """NVIDIA Container Toolkit — DeviceRequests API.

    Requires ``nvidia-container-toolkit`` installed on the host and the
    Docker daemon configured with ``nvidia`` as a runtime or via CDI.
    """
    device_ids: list[str]
    if gpu_devices == "all":
        device_ids = ["all"]
    else:
        device_ids = [str(d) for d in gpu_devices]

    return {
        "DeviceRequests": [
            {
                "Driver": "nvidia",
                "DeviceIDs": device_ids,
                "Capabilities": [["gpu"]],
            },
        ],
    }


def _amd_config(gpu_devices: str | list[int]) -> dict[str, Any]:
    """AMD ROCm — direct device mounts.

    ROCm requires:
    - ``/dev/kfd`` — Kernel Fusion Driver (compute dispatch).
    - ``/dev/dri/renderD*`` — DRM render nodes (one per GPU).
    - Groups ``video`` and ``render`` for device file access.
    - ``seccomp=unconfined`` for ROCm's memory management.
    """
    devices: list[str] = ["/dev/kfd"]

    if gpu_devices == "all":
        # Expose all render nodes
        devices.append("/dev/dri")
    else:
        # Expose specific render nodes (renderD128 = GPU 0, renderD129 = GPU 1, …)
        for idx in gpu_devices:
            render_node = f"/dev/dri/renderD{128 + idx}"
            devices.append(render_node)
        # Also add the card device for display/info
        for idx in gpu_devices:
            devices.append(f"/dev/dri/card{idx}")

    return {
        "Devices": [{"PathOnHost": d, "PathInContainer": d, "CgroupPermissions": "rwm"} for d in devices],
        "GroupAdd": ["video", "render"],
        "SecurityOpt": ["seccomp=unconfined"],
    }


def _intel_config(gpu_devices: str | list[int]) -> dict[str, Any]:
    """Intel oneAPI / Level Zero — direct device mounts.

    Intel GPUs (Arc, Flex, Data Center Max) use the i915 or xe kernel
    driver.  Exposing ``/dev/dri`` is usually sufficient.
    """
    devices: list[str] = []

    if gpu_devices == "all":
        devices.append("/dev/dri")
    else:
        for idx in gpu_devices:
            devices.append(f"/dev/dri/renderD{128 + idx}")
            devices.append(f"/dev/dri/card{idx}")

    return {
        "Devices": [{"PathOnHost": d, "PathInContainer": d, "CgroupPermissions": "rwm"} for d in devices],
        "GroupAdd": ["video", "render"],
    }
