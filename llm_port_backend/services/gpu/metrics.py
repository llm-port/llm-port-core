"""GPU metrics collection — vendor-agnostic, multi-platform.

Collects GPU utilization and VRAM usage using the best available
strategy for the detected GPU vendor and operating system:

1. **pynvml** — NVIDIA GPUs on any OS.
2. **ROCm sysfs** — AMD GPUs on Linux.
3. **Windows Performance Counters** — any GPU on Windows 10+.
4. Returns ``None`` values if no method succeeds.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys

from llm_port_backend.services.gpu.types import GpuMetrics

logger = logging.getLogger(__name__)


def collect_gpu_metrics() -> GpuMetrics:
    """Collect point-in-time GPU utilization and VRAM usage.

    Tries multiple strategies in preference order and returns as soon
    as one succeeds.
    """
    # Strategy 1: NVIDIA pynvml
    with contextlib.suppress(ImportError, Exception):
        result = _metrics_nvidia()
        if result.util_percent is not None:
            return result

    # Strategy 2: AMD ROCm sysfs (Linux)
    if sys.platform == "linux":
        with contextlib.suppress(Exception):
            result = _metrics_amd_linux()
            if result.util_percent is not None:
                return result

    # Strategy 3: Windows Performance Counters (any vendor)
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            result = _metrics_windows()
            if result.util_percent is not None:
                return result

    return GpuMetrics()


# ── Strategy 1: NVIDIA pynvml ─────────────────────────────────────────


def _metrics_nvidia() -> GpuMetrics:
    """Collect NVIDIA GPU metrics via pynvml."""
    import pynvml  # noqa: PLC0415

    pynvml.nvmlInit()
    try:
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            return GpuMetrics()

        total_util = 0.0
        total_vram_used = 0
        total_vram_total = 0
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            total_util += float(util.gpu)
            total_vram_used += int(mem.used)
            total_vram_total += int(mem.total)

        return GpuMetrics(
            util_percent=round(total_util / count, 1),
            vram_used_bytes=total_vram_used,
            vram_total_bytes=total_vram_total,
        )
    finally:
        pynvml.nvmlShutdown()


# ── Strategy 2: AMD ROCm sysfs (Linux) ───────────────────────────────


def _metrics_amd_linux() -> GpuMetrics:
    """Collect AMD GPU metrics from Linux sysfs.

    Reads:
    - ``/sys/class/drm/card*/device/gpu_busy_percent`` — utilization
    - ``/sys/class/drm/card*/device/mem_info_vram_used`` — VRAM used
    - ``/sys/class/drm/card*/device/mem_info_vram_total`` — VRAM total

    These files are exposed by the amdgpu kernel driver and are
    available without ROCm userspace tools.
    """
    from pathlib import Path  # noqa: PLC0415

    drm_base = Path("/sys/class/drm")
    if not drm_base.exists():
        return GpuMetrics()

    utils: list[float] = []
    vram_used_total = 0
    vram_total_total = 0

    for card_dir in sorted(drm_base.iterdir()):
        if not card_dir.name.startswith("card") or "-" in card_dir.name:
            continue

        device_dir = card_dir / "device"
        vendor_file = device_dir / "vendor"
        if not vendor_file.exists():
            continue

        with contextlib.suppress(Exception):
            if vendor_file.read_text().strip() != "0x1002":
                continue

        # GPU utilization
        busy_file = device_dir / "gpu_busy_percent"
        if busy_file.exists():
            with contextlib.suppress(Exception):
                utils.append(float(busy_file.read_text().strip()))

        # VRAM used
        vram_used_file = device_dir / "mem_info_vram_used"
        if vram_used_file.exists():
            with contextlib.suppress(Exception):
                vram_used_total += int(vram_used_file.read_text().strip())

        # VRAM total
        vram_total_file = device_dir / "mem_info_vram_total"
        if vram_total_file.exists():
            with contextlib.suppress(Exception):
                vram_total_total += int(vram_total_file.read_text().strip())

    if not utils:
        return GpuMetrics()

    avg_util = round(sum(utils) / len(utils), 1)
    return GpuMetrics(
        util_percent=avg_util,
        vram_used_bytes=vram_used_total if vram_used_total > 0 else None,
        vram_total_bytes=vram_total_total if vram_total_total > 0 else None,
    )


# ── Strategy 3: Windows Performance Counters ─────────────────────────


def _metrics_windows() -> GpuMetrics:
    """Collect GPU metrics from Windows Performance Counters.

    Uses ``Get-Counter`` to query:
    - ``\\GPU Engine(*engtype_3D)\\Utilization Percentage``
    - ``\\GPU Local Adapter Memory(*)\\Local Usage``

    Works for AMD, Intel, and NVIDIA GPUs on Windows 10+.
    """
    ps_script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$u=(Get-Counter '\\GPU Engine(*engtype_3D)\\Utilization Percentage'"
        " -ErrorAction SilentlyContinue).CounterSamples"
        " | Measure-Object -Property CookedValue -Sum;"
        "$m=(Get-Counter '\\GPU Local Adapter Memory(*)\\Local Usage'"
        " -ErrorAction SilentlyContinue).CounterSamples"
        " | Measure-Object -Property CookedValue -Sum;"
        'Write-Output "$($u.Sum)|$($m.Sum)"'
    )

    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
        timeout=8,
    )

    util: float | None = None
    vram_used: int | None = None
    vram_total: int | None = None

    if proc.returncode == 0 and proc.stdout.strip():
        parts = proc.stdout.strip().split("|")
        if len(parts) >= 2:
            util_str = parts[0].strip().replace(",", ".")
            vram_str = parts[1].strip().replace(",", ".")
            if util_str:
                with contextlib.suppress(ValueError):
                    util = round(float(util_str), 1)
            if vram_str:
                with contextlib.suppress(ValueError):
                    vram_used = int(float(vram_str))

    # Total VRAM — prefer 64-bit registry value over 32-bit WMI
    if util is not None:
        with contextlib.suppress(Exception):
            proc2 = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "$r = Get-ItemProperty"
                    " 'HKLM:\\SYSTEM\\ControlSet001\\Control\\Class"
                    "\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0*'"
                    " -Name 'HardwareInformation.qwMemorySize'"
                    " -ErrorAction SilentlyContinue"
                    " | Select-Object -First 1"
                    " -ExpandProperty 'HardwareInformation.qwMemorySize';"
                    "if ($r) { $r } else {"
                    " (Get-CimInstance Win32_VideoController"
                    " | Select-Object -First 1 -ExpandProperty AdapterRAM) }",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc2.returncode == 0 and proc2.stdout.strip():
                vram_total = int(proc2.stdout.strip())

    return GpuMetrics(
        util_percent=util,
        vram_used_bytes=vram_used,
        vram_total_bytes=vram_total,
    )
