"""Unit tests for the GPU detector core (merge + inventory selection).

Covers ``_merge_device_lists`` (duplicate detection and "keep richer VRAM")
and ``_build_inventory`` (primary-vendor priority tie-breaking).  These are
pure; no platform probing happens.  The real ``GpuDevice``/``GpuVendor``/
``GpuComputeApi``/``GpuInventory`` types are used so the fixture is faithful.
"""

from __future__ import annotations

from llm_port_backend.services.gpu.detector import (
    _build_inventory,
    _merge_device_lists,
)
from llm_port_backend.services.gpu.types import (
    GpuComputeApi,
    GpuDevice,
    GpuVendor,
)


def _dev(vendor: GpuVendor, model: str, vram: int = 0, api: GpuComputeApi = GpuComputeApi.UNKNOWN) -> GpuDevice:
    return GpuDevice(index=0, vendor=vendor, model=model, vram_bytes=vram, compute_api=api)


GB = 1 << 30


# ──────────────────────────────────────────────────────────────────────────────
# _merge_device_lists
# ──────────────────────────────────────────────────────────────────────────────


def test_merge_empty_new_returns_existing() -> None:
    existing = [_dev(GpuVendor.NVIDIA, "A100", vram=40 * GB)]
    assert _merge_device_lists(existing, []) is not None
    assert len(_merge_device_lists(existing, [])) == 1


def test_merge_appends_non_duplicate() -> None:
    existing = [_dev(GpuVendor.NVIDIA, "A100", vram=40 * GB)]
    new = [_dev(GpuVendor.AMD, "Radeon Pro W6800", vram=32 * GB)]
    merged = _merge_device_lists(existing, new)
    assert len(merged) == 2


def test_merge_duplicate_keeps_higher_vram() -> None:
    existing = [_dev(GpuVendor.NVIDIA, "A100 40GB", vram=40 * GB)]
    # Same vendor, model name as a substring of the existing name.
    # new has MORE VRAM (80GB) so it should replace the existing 40GB entry.
    new = [_dev(GpuVendor.NVIDIA, "A100", vram=80 * GB)]
    merged = _merge_device_lists(existing, new)
    assert len(merged) == 1
    # The higher-VRAM entry wins.
    assert merged[0].vram_bytes == 80 * GB


def test_merge_duplicate_keeps_existing_when_new_has_less_or_equal_vram() -> None:
    existing = [_dev(GpuVendor.NVIDIA, "A100 80GB", vram=80 * GB)]
    # Less VRAM AND equal VRAM both keep the existing entry (only strict > replaces).
    for vram in (40 * GB, 80 * GB):
        new = [_dev(GpuVendor.NVIDIA, "A100", vram=vram)]
        merged = _merge_device_lists(existing, new)
        assert merged[0].vram_bytes == 80 * GB


def test_merge_does_not_dedupe_across_vendors() -> None:
    # Same model name but different vendors are both kept.
    existing = [_dev(GpuVendor.NVIDIA, "A100", vram=40 * GB)]
    new = [_dev(GpuVendor.AMD, "A100", vram=40 * GB)]
    assert len(_merge_device_lists(existing, new)) == 2


def test_merge_same_vendor_model_fuzzy_substring_both_ways() -> None:
    # Case-insensitive substring in either direction.
    existing = [_dev(GpuVendor.AMD, "Radeon RX 7900 XTX", vram=24 * GB)]
    # "7900 xtx" substring of "Radeon RX 7900 XTX" (lowercased)
    new = [_dev(GpuVendor.AMD, "7900 xtx", vram=24 * GB)]
    assert len(_merge_device_lists(existing, new)) == 1


def test_merge_same_vendor_but_different_model_appended() -> None:
    existing = [_dev(GpuVendor.NVIDIA, "A100", vram=40 * GB)]
    new = [_dev(GpuVendor.NVIDIA, "H100", vram=80 * GB)]  # no overlap → append
    merged = _merge_device_lists(existing, new)
    assert len(merged) == 2
    # Both entries are present (order: existing first, then new).
    assert {d.model for d in merged} == {"A100", "H100"}


# ──────────────────────────────────────────────────────────────────────────────
# _build_inventory
# ──────────────────────────────────────────────────────────────────────────────


def test_build_inventory_empty_defaults_to_unknown() -> None:
    inv = _build_inventory([])
    assert inv.has_gpu is False
    assert inv.primary_vendor is GpuVendor.UNKNOWN
    assert inv.primary_compute_api is GpuComputeApi.UNKNOWN
    assert inv.device_count == 0


def test_build_inventory_single_nvidia_device() -> None:
    inv = _build_inventory([_dev(GpuVendor.NVIDIA, "A100", vram=40 * GB, api=GpuComputeApi.CUDA)])
    assert inv.has_gpu is True
    assert inv.primary_vendor is GpuVendor.NVIDIA
    assert inv.primary_compute_api is GpuComputeApi.CUDA
    assert inv.device_count == 1
    assert inv.total_vram_bytes == 40 * GB


def test_build_inventory_nvidia_beats_amd() -> None:
    inv = _build_inventory(
        [
            _dev(GpuVendor.AMD, "Radeon", vram=16 * GB, api=GpuComputeApi.ROCM),
            _dev(GpuVendor.NVIDIA, "A100", vram=40 * GB, api=GpuComputeApi.CUDA),
        ]
    )
    assert inv.primary_vendor is GpuVendor.NVIDIA
    assert inv.primary_compute_api is GpuComputeApi.CUDA


def test_build_inventory_amd_beats_intel_when_no_nvidia() -> None:
    inv = _build_inventory(
        [
            _dev(GpuVendor.INTEL, "Arc A750", vram=16 * GB, api=GpuComputeApi.ONEAPI),
            _dev(GpuVendor.AMD, "RX 7900", vram=24 * GB, api=GpuComputeApi.ROCM),
        ]
    )
    assert inv.primary_vendor is GpuVendor.AMD
    assert inv.primary_compute_api is GpuComputeApi.ROCM


def test_build_inventory_amd_beats_apple_when_no_discrete() -> None:
    inv = _build_inventory(
        [
            _dev(GpuVendor.APPLE, "Apple M2 Max", vram=0, api=GpuComputeApi.METAL),
            _dev(GpuVendor.AMD, "Radeon Pro", vram=16 * GB, api=GpuComputeApi.ROCM),
        ]
    )
    assert inv.primary_vendor is GpuVendor.AMD

def test_build_inventory_same_vendor_vram_tie_break_prefers_higher_vram_compute_api() -> None:
    # Both AMD, both same vendor priority.  The *compute API* of the primary
    # is the observable output.  Place the higher-VRAM card FIRST and the
    # lower-VRAM card SECOND — the max() over (priority, vram_bytes) should
    # still pick the 24GB one, so primary_compute_api must be CUDA (not Vulkan).
    higher_vram_cuda = _dev(GpuVendor.AMD, "RX 7900 XTX", vram=24 * GB, api=GpuComputeApi.CUDA)
    lower_vram_vulkan = _dev(GpuVendor.AMD, "RX 6600", vram=8 * GB, api=GpuComputeApi.VULKAN)
    inv = _build_inventory([higher_vram_cuda, lower_vram_vulkan])
    assert inv.primary_vendor is GpuVendor.AMD
    # This is the real assertion: higher VRAM wins tie-break even though both
    # are AMD (same priority).
    assert inv.primary_compute_api is GpuComputeApi.CUDA
    # Reversed order produces the same result — the 8GB Vulkan card can't win.
    inv_rev = _build_inventory([lower_vram_vulkan, higher_vram_cuda])
    assert inv_rev.primary_compute_api is GpuComputeApi.CUDA


def test_build_inventory_total_vram_sums_all_devices() -> None:
    inv = _build_inventory(
        [
            _dev(GpuVendor.AMD, "RX 6600", vram=8 * GB),
            _dev(GpuVendor.AMD, "RX 7900", vram=24 * GB),
            _dev(GpuVendor.NVIDIA, "A100", vram=40 * GB),
        ]
    )
    assert inv.total_vram_bytes == (8 + 24 + 40) * GB
    assert inv.device_count == 3
    # Even with AMD cards present, NVIDIA is still the primary.
    assert inv.primary_vendor is GpuVendor.NVIDIA


def test_build_inventory_retains_all_devices_list() -> None:
    a = _dev(GpuVendor.NVIDIA, "A100", vram=40 * GB)
    b = _dev(GpuVendor.AMD, "RX 7900", vram=24 * GB)
    inv = _build_inventory([a, b])
    assert set(inv.devices) == {a, b}
