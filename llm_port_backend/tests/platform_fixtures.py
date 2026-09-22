"""The platform facts a node agent reports, for tests that need a real node.

A runtime bundle is resolved from a machine's CPU architecture and its
accelerator vendor, so a node built with an empty ``capabilities_json``
resolves to nothing and the cluster lifecycle refuses it -- correctly, but for
a reason that has nothing to do with what most of these tests are about.

These are the fields a live agent actually reports, copied from the shape of
``infra_node.capabilities_json`` on the DGX pair: ``machine``, ``gpu_vendor``
and ``gpu_count`` at the top level, with no per-device list (the agent does
not report compute capabilities, which is why bundle matching does not
require them).
"""

from __future__ import annotations

from typing import Any

#: A DGX Spark GB10: aarch64, NVIDIA. Resolves to the certified DGX bundle.
DGX_SPARK_PLATFORM: dict[str, Any] = {
    "machine": "aarch64",
    "os": "Linux",
    "gpu_vendor": "nvidia",
    "gpu_count": 1,
    "container_runtime": "docker",
    "docker_available": True,
}

#: An x86_64 workstation with an NVIDIA card. Resolves to the generic bundle.
X86_NVIDIA_PLATFORM: dict[str, Any] = {
    "machine": "x86_64",
    "os": "Linux",
    "gpu_vendor": "nvidia",
    "gpu_count": 1,
    "container_runtime": "docker",
    "docker_available": True,
}


def with_platform(
    capabilities: dict[str, Any] | None = None,
    *,
    platform: dict[str, Any] = DGX_SPARK_PLATFORM,
) -> dict[str, Any]:
    """Merge *platform* under whatever else a test wants to report.

    The test's own keys win, so a case that deliberately reports a machine
    with no accelerator can still say so.
    """
    return {**platform, **(capabilities or {})}


__all__ = ["DGX_SPARK_PLATFORM", "X86_NVIDIA_PLATFORM", "with_platform"]
