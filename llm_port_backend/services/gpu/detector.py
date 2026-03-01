"""GPU auto-detection — discovers installed GPUs across vendors.

Detection strategies (executed in order):
1. **pynvml** — NVIDIA GPUs on any OS.
2. **ROCm sysfs** — AMD GPUs on Linux with ROCm installed.
3. **Windows WMI / registry** — any GPU on Windows 10+.
4. **macOS system_profiler** — Apple GPUs on macOS (future).
5. Falls back to an empty inventory.

The result is cached for the lifetime of the process because GPU
hardware doesn't change at runtime.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import platform
import subprocess
import sys
from pathlib import Path

from llm_port_backend.services.gpu.types import (
    GpuComputeApi,
    GpuDevice,
    GpuInventory,
    GpuVendor,
)

logger = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def detect_gpus() -> GpuInventory:
    """Auto-detect all GPUs on the host.

    Returns a cached :class:`GpuInventory` with one :class:`GpuDevice`
    per physical GPU found.  The ``primary_vendor`` and
    ``primary_compute_api`` are derived from the first device.
    """
    devices: list[GpuDevice] = []

    # Strategy 1: NVIDIA (pynvml)
    devices = _detect_nvidia()
    if devices:
        return _build_inventory(devices)

    # Strategy 2: AMD ROCm (Linux sysfs)
    if sys.platform == "linux":
        devices = _detect_amd_rocm()
        if devices:
            return _build_inventory(devices)

    # Strategy 3: Windows (WMI + DX diagnostics)
    if sys.platform == "win32":
        devices = _detect_windows_wmi()
        if devices:
            return _build_inventory(devices)

    # Strategy 4: macOS (system_profiler)
    if sys.platform == "darwin":
        devices = _detect_macos()
        if devices:
            return _build_inventory(devices)

    # Strategy 5: Fallback — try Intel on Linux
    if sys.platform == "linux":
        devices = _detect_intel_linux()
        if devices:
            return _build_inventory(devices)

    logger.info("No GPUs detected on this host")
    return GpuInventory()


# ── Strategy 1: NVIDIA pynvml ─────────────────────────────────────────


def _detect_nvidia() -> list[GpuDevice]:
    """Detect NVIDIA GPUs using pynvml (works on any OS with drivers)."""
    devices: list[GpuDevice] = []
    with contextlib.suppress(ImportError, Exception):
        import pynvml  # noqa: PLC0415

        pynvml.nvmlInit()
        try:
            driver = pynvml.nvmlSystemGetDriverVersion()
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode()
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                devices.append(
                    GpuDevice(
                        index=i,
                        vendor=GpuVendor.NVIDIA,
                        model=name,
                        vram_bytes=int(mem.total),
                        driver_version=driver if isinstance(driver, str) else driver.decode(),
                        compute_api=GpuComputeApi.CUDA,
                    )
                )
        finally:
            pynvml.nvmlShutdown()
    if devices:
        logger.info("Detected %d NVIDIA GPU(s) via pynvml", len(devices))
    return devices


# ── Strategy 2: AMD ROCm (Linux sysfs) ───────────────────────────────


def _detect_amd_rocm() -> list[GpuDevice]:
    """Detect AMD GPUs via ROCm sysfs and rocm-smi on Linux."""
    devices: list[GpuDevice] = []

    # Check if /sys/class/drm/card*/device/vendor is AMD (0x1002)
    drm_base = Path("/sys/class/drm")
    if not drm_base.exists():
        return devices

    # Also check for /dev/kfd which indicates ROCm kernel driver
    has_kfd = Path("/dev/kfd").exists()

    card_indices: list[int] = []
    for card_dir in sorted(drm_base.iterdir()):
        if not card_dir.name.startswith("card") or card_dir.name.count("-") > 0:
            continue
        vendor_file = card_dir / "device" / "vendor"
        if not vendor_file.exists():
            continue
        with contextlib.suppress(Exception):
            vendor_id = vendor_file.read_text().strip()
            if vendor_id == "0x1002":  # AMD vendor ID
                card_idx = int(card_dir.name.replace("card", ""))
                card_indices.append(card_idx)

    if not card_indices:
        return devices

    # Try rocm-smi for detailed info
    rocm_info = _rocm_smi_info()

    # Determine ROCm driver version
    driver_version = ""
    with contextlib.suppress(Exception):
        proc = subprocess.run(
            ["rocm-smi", "--showdriverversion"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if "Driver version" in line:
                    driver_version = line.split(":")[-1].strip()
                    break

    compute_api = GpuComputeApi.ROCM if has_kfd else GpuComputeApi.UNKNOWN

    for idx, card_idx in enumerate(card_indices):
        model_name = "AMD GPU"
        vram_bytes = 0

        # Try rocm-smi info first
        if idx < len(rocm_info):
            model_name = rocm_info[idx].get("name", model_name)
            vram_bytes = rocm_info[idx].get("vram", 0)
        else:
            # Fallback: read from sysfs
            card_dir = drm_base / f"card{card_idx}" / "device"
            with contextlib.suppress(Exception):
                product_file = card_dir / "product_name"
                if product_file.exists():
                    model_name = product_file.read_text().strip()

            with contextlib.suppress(Exception):
                vram_file = card_dir / "mem_info_vram_total"
                if vram_file.exists():
                    vram_bytes = int(vram_file.read_text().strip())

        devices.append(
            GpuDevice(
                index=idx,
                vendor=GpuVendor.AMD,
                model=model_name,
                vram_bytes=vram_bytes,
                driver_version=driver_version,
                compute_api=compute_api,
            )
        )

    if devices:
        logger.info("Detected %d AMD GPU(s) via ROCm sysfs", len(devices))
    return devices


def _rocm_smi_info() -> list[dict[str, object]]:
    """Parse ``rocm-smi --showproductname --showmeminfo vram`` output."""
    info: list[dict[str, object]] = []
    with contextlib.suppress(FileNotFoundError, Exception):
        proc = subprocess.run(
            ["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--csv"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return info
        lines = proc.stdout.strip().splitlines()
        if len(lines) < 2:
            return info
        # CSV header + data rows
        headers = [h.strip().lower() for h in lines[0].split(",")]
        for row in lines[1:]:
            cols = [c.strip() for c in row.split(",")]
            if len(cols) < len(headers):
                continue
            entry: dict[str, object] = {}
            for h, c in zip(headers, cols):
                if "card" in h or "device" in h:
                    entry["name"] = c
                if "total" in h and "vram" in h:
                    with contextlib.suppress(ValueError):
                        entry["vram"] = int(c)
            if entry:
                info.append(entry)
    return info


# ── Strategy 3: Windows WMI ──────────────────────────────────────────


def _detect_windows_wmi() -> list[GpuDevice]:
    """Detect GPUs on Windows using WMI (Win32_VideoController).

    Works for NVIDIA, AMD, and Intel GPUs.  Uses two PowerShell steps:
    1.  Basic GPU enumeration via ``Get-CimInstance Win32_VideoController``.
    2.  64-bit VRAM from the registry (optional — fixes AdapterRAM wrapping
        at 4 GB on older WMI builds).
    """
    devices: list[GpuDevice] = []
    with contextlib.suppress(Exception):
        # Step 1 — enumerate GPUs (simple, no inline comments)
        ps_script = (
            "Get-CimInstance Win32_VideoController"
            " | ForEach-Object {"
            " '{0}|{1}|{2}|{3}' -f $_.Name, $_.AdapterRAM,"
            " $_.DriverVersion, $_.PNPDeviceID }"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            logger.debug("WMI GPU query failed (rc=%d): %s", proc.returncode, proc.stderr[:200])
            return devices

        # Step 2 — try 64-bit VRAM from registry (>4 GB GPUs)
        qw_vram: int | None = None
        with contextlib.suppress(Exception):
            reg_script = (
                "$r = Get-ItemProperty"
                " 'HKLM:\\SYSTEM\\ControlSet001\\Control\\Class"
                "\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0*'"
                " -Name 'HardwareInformation.qwMemorySize'"
                " -ErrorAction SilentlyContinue"
                " | Select-Object -First 1"
                " -ExpandProperty 'HardwareInformation.qwMemorySize';"
                " if ($r) { $r }"
            )
            reg_proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", reg_script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if reg_proc.returncode == 0 and reg_proc.stdout.strip():
                qw_vram = int(reg_proc.stdout.strip())

        for idx, line in enumerate(proc.stdout.strip().splitlines()):
            parts = line.split("|")
            if len(parts) < 4:
                continue
            name = parts[0].strip()
            vram_str = parts[1].strip()
            driver = parts[2].strip()
            pnp = parts[3].strip().upper()

            vram_bytes = 0
            with contextlib.suppress(ValueError):
                vram_bytes = int(vram_str)

            # Prefer 64-bit registry VRAM if available and larger
            if qw_vram and qw_vram > vram_bytes:
                vram_bytes = qw_vram

            vendor, compute = _classify_windows_gpu(name, pnp)

            # Skip virtual / software adapters
            if "microsoft" in name.lower() and "basic" in name.lower():
                continue
            if "virtual" in name.lower():
                continue

            devices.append(
                GpuDevice(
                    index=idx,
                    vendor=vendor,
                    model=name,
                    vram_bytes=vram_bytes,
                    driver_version=driver,
                    compute_api=compute,
                )
            )

    if devices:
        logger.info("Detected %d GPU(s) via Windows WMI", len(devices))
    return devices


def _classify_windows_gpu(name: str, pnp_id: str) -> tuple[GpuVendor, GpuComputeApi]:
    """Classify a Windows GPU by name and PnP device ID."""
    name_lower = name.lower()
    pnp_upper = pnp_id.upper()

    # NVIDIA: PCI\VEN_10DE
    if "VEN_10DE" in pnp_upper or "nvidia" in name_lower or "geforce" in name_lower:
        return GpuVendor.NVIDIA, GpuComputeApi.CUDA
    # AMD: PCI\VEN_1002
    if "VEN_1002" in pnp_upper or "amd" in name_lower or "radeon" in name_lower:
        return GpuVendor.AMD, GpuComputeApi.ROCM
    # Intel: PCI\VEN_8086
    if "VEN_8086" in pnp_upper or "intel" in name_lower:
        return GpuVendor.INTEL, GpuComputeApi.ONEAPI

    return GpuVendor.UNKNOWN, GpuComputeApi.UNKNOWN


# ── Strategy 4: macOS ────────────────────────────────────────────────


def _detect_macos() -> list[GpuDevice]:
    """Detect GPUs on macOS using system_profiler.

    Apple Silicon GPUs use Metal; discrete/integrated Intel GPUs are
    reported but Metal is the only useful compute API on modern macOS.
    """
    devices: list[GpuDevice] = []
    with contextlib.suppress(Exception):
        import json as _json  # noqa: PLC0415

        proc = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return devices

        data = _json.loads(proc.stdout)
        displays = data.get("SPDisplaysDataType", [])
        for idx, gpu in enumerate(displays):
            name = gpu.get("sppci_model", "Unknown GPU")
            vendor_str = gpu.get("spdisplays_vendor", "").lower()
            vram_str = gpu.get("spdisplays_vram", "0")

            # Parse VRAM string like "8 GB" or "16384 MB"
            vram_bytes = 0
            with contextlib.suppress(ValueError):
                parts = vram_str.split()
                val = int(parts[0])
                unit = parts[1].upper() if len(parts) > 1 else "MB"
                if "GB" in unit:
                    vram_bytes = val * 1024 * 1024 * 1024
                elif "MB" in unit:
                    vram_bytes = val * 1024 * 1024
                else:
                    vram_bytes = val

            if "apple" in vendor_str or "apple" in name.lower():
                vendor = GpuVendor.APPLE
                compute = GpuComputeApi.METAL
            elif "amd" in vendor_str or "radeon" in name.lower():
                vendor = GpuVendor.AMD
                compute = GpuComputeApi.METAL  # On macOS, even AMD uses Metal
            elif "intel" in vendor_str or "intel" in name.lower():
                vendor = GpuVendor.INTEL
                compute = GpuComputeApi.METAL
            else:
                vendor = GpuVendor.UNKNOWN
                compute = GpuComputeApi.METAL

            devices.append(
                GpuDevice(
                    index=idx,
                    vendor=vendor,
                    model=name,
                    vram_bytes=vram_bytes,
                    driver_version=platform.mac_ver()[0],
                    compute_api=compute,
                )
            )

    if devices:
        logger.info("Detected %d GPU(s) via macOS system_profiler", len(devices))
    return devices


# ── Strategy 5: Intel on Linux (sysfs / clinfo) ──────────────────────


def _detect_intel_linux() -> list[GpuDevice]:
    """Detect Intel GPUs on Linux via DRI sysfs (render nodes)."""
    devices: list[GpuDevice] = []
    drm_base = Path("/sys/class/drm")
    if not drm_base.exists():
        return devices

    for card_dir in sorted(drm_base.iterdir()):
        if not card_dir.name.startswith("card") or "-" in card_dir.name:
            continue
        vendor_file = card_dir / "device" / "vendor"
        if not vendor_file.exists():
            continue
        with contextlib.suppress(Exception):
            vendor_id = vendor_file.read_text().strip()
            if vendor_id == "0x8086":  # Intel vendor ID
                product = "Intel GPU"
                product_file = card_dir / "device" / "product_name"
                if product_file.exists():
                    product = product_file.read_text().strip()

                idx = int(card_dir.name.replace("card", ""))
                devices.append(
                    GpuDevice(
                        index=idx,
                        vendor=GpuVendor.INTEL,
                        model=product,
                        vram_bytes=0,  # Intel integrated GPUs share system RAM
                        driver_version="",
                        compute_api=GpuComputeApi.ONEAPI,
                    )
                )

    if devices:
        logger.info("Detected %d Intel GPU(s) via Linux sysfs", len(devices))
    return devices


# ── Internal helper ───────────────────────────────────────────────────


def _build_inventory(devices: list[GpuDevice]) -> GpuInventory:
    """Build a :class:`GpuInventory` from a list of devices."""
    primary_vendor = devices[0].vendor if devices else GpuVendor.UNKNOWN
    primary_compute = devices[0].compute_api if devices else GpuComputeApi.UNKNOWN
    return GpuInventory(
        devices=devices,
        primary_vendor=primary_vendor,
        primary_compute_api=primary_compute,
    )
