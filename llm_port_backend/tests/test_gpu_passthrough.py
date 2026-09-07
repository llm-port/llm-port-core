"""Unit tests for `build_gpu_host_config` (vendor → Docker host-config mapping).

Pure translation logic.  The AMD/Intel Linux device-mount branches are
platform-guarded, so on this (Windows) test host they take the
``sys.platform != "linux"`` path — those are the cases exercised here.  The
Linux device-assembly logic is verified separately by monkeypatching
``sys.platform`` on an AMD/Intel host where the dev nodes exist, but on CI
(non-Linux) we assert the non-Linux contract and the platform guard.
"""

from __future__ import annotations

import sys

import pytest

from llm_port_backend.services.gpu.passthrough import build_gpu_host_config
from llm_port_backend.services.gpu.types import GpuVendor


# ──────────────────────────────────────────────────────────────────────────────
# Trivial / guarded branches
# ──────────────────────────────────────────────────────────────────────────────


def test_none_devices_returns_empty() -> None:
    assert build_gpu_host_config(GpuVendor.NVIDIA, None) == {}
    assert build_gpu_host_config(GpuVendor.AMD, None) == {}


def test_apple_metal_not_supported() -> None:
    with pytest.raises(NotImplementedError, match="Apple Metal"):
        build_gpu_host_config(GpuVendor.APPLE, "all")


# ──────────────────────────────────────────────────────────────────────────────
# NVIDIA — DeviceRequests, platform-independent
# ──────────────────────────────────────────────────────────────────────────────


def test_nvidia_all() -> None:
    assert build_gpu_host_config(GpuVendor.NVIDIA, "all") == {
        "DeviceRequests": [
            {"Driver": "nvidia", "DeviceIDs": ["all"], "Capabilities": [["gpu"]]},
        ]
    }


def test_nvidia_specific_indices() -> None:
    assert build_gpu_host_config(GpuVendor.NVIDIA, [0, 1]) == {
        "DeviceRequests": [
            {"Driver": "nvidia", "DeviceIDs": ["0", "1"], "Capabilities": [["gpu"]]},
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# UNKNOWN — best-effort NVIDIA fallback
# ──────────────────────────────────────────────────────────────────────────────


def test_unknown_falls_back_to_nvidia() -> None:
    result = build_gpu_host_config(GpuVendor.UNKNOWN, "all")
    assert result == {
        "DeviceRequests": [
            {"Driver": "nvidia", "DeviceIDs": ["all"], "Capabilities": [["gpu"]]},
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# AMD / Intel — platform guard (non-Linux hosts skip raw device mounts)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "linux", reason="AMD non-Linux contract only")
def test_amd_non_linux_only_security_opt() -> None:
    assert build_gpu_host_config(GpuVendor.AMD, "all") == {"SecurityOpt": ["seccomp=unconfined"]}
    assert build_gpu_host_config(GpuVendor.AMD, [0]) == {"SecurityOpt": ["seccomp=unconfined"]}


@pytest.mark.skipif(sys.platform == "linux", reason="Intel non-Linux contract only")
def test_intel_non_linux_returns_empty() -> None:
    assert build_gpu_host_config(GpuVendor.INTEL, "all") == {}
    assert build_gpu_host_config(GpuVendor.INTEL, [0]) == {}


# Linux-only device-assembly logic.  We force the branch by monkeypatching the
# ``sys.platform`` value the function reads and the ``Path("/dev/kfd").exists``
# probe, so the mount list can be asserted deterministically on any host CI
# runs on.
def test_amd_linux_all_exposes_dri_and_kfd(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "exists", lambda self: True)  # /dev/kfd present

    result = build_gpu_host_config(GpuVendor.AMD, "all")

    assert result["GroupAdd"] == ["video", "render"]
    assert result["SecurityOpt"] == ["seccomp=unconfined"]
    # "all" → expose the whole /dev/dri, plus /dev/kfd (probed present above)
    paths = {d["PathOnHost"] for d in result["Devices"]}
    assert paths == {"/dev/kfd", "/dev/dri"}
    assert all(d["CgroupPermissions"] == "rwm" for d in result["Devices"])


def test_amd_linux_specific_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "exists", lambda self: False)  # kfd absent → only /dev/dri

    result = build_gpu_host_config(GpuVendor.AMD, [0, 1])
    paths = [d["PathOnHost"] for d in result["Devices"]]
    # render nodes first, then card devices
    assert paths == ["/dev/dri/renderD128", "/dev/dri/renderD129", "/dev/dri/card0", "/dev/dri/card1"]
    assert result["GroupAdd"] == ["video", "render"]
