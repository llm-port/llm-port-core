"""GPU auto-detection — discovers installed GPUs across vendors.

Detection strategies are combined so that **all** GPUs are reported,
even on multi-vendor laptops (e.g. AMD iGPU + NVIDIA dGPU):

1. **pynvml** — NVIDIA GPUs on any OS (rich data: VRAM, driver, etc.).
2. **ROCm sysfs** — AMD GPUs on Linux with ROCm installed.
3. **Windows WMI / registry** — any GPU on Windows 10+.
4. **macOS system_profiler** — Apple GPUs on macOS.
5. **Intel sysfs** — Intel GPUs on Linux.
6. **Docker daemon** — containerised fallback.  When the backend runs
   inside a Docker container the host GPUs are not directly visible.
   This strategy queries the Docker daemon (via the bind-mounted
   ``/var/run/docker.sock``) for registered GPU runtimes (e.g. the
   NVIDIA Container Toolkit) so ``has_gpu`` returns ``True`` and the
   correct vLLM image is selected.

Results from all applicable strategies are merged and de-duplicated so
that a laptop with an AMD iGPU and an NVIDIA dGPU correctly reports
both.  The result is cached but can be refreshed via
:func:`redetect_gpus`.
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
    """Auto-detect **all** GPUs on the host.

    Runs every applicable detection strategy and merges the results so
    multi-vendor setups (e.g. AMD iGPU + NVIDIA dGPU) are fully
    reported.

    Returns a cached :class:`GpuInventory` with one :class:`GpuDevice`
    per physical GPU found.  The ``primary_vendor`` and
    ``primary_compute_api`` are derived from the most capable device
    (discrete NVIDIA/AMD preferred over integrated).

    Use :func:`redetect_gpus` to clear the cache and re-run detection
    (e.g. after a GPU is powered on or a driver is installed).
    """
    devices: list[GpuDevice] = []

    # Strategy 1: NVIDIA (pynvml) — works on any OS with NVIDIA drivers
    nvidia_devices = _detect_nvidia()
    devices.extend(nvidia_devices)

    # Strategy 2: AMD ROCm (Linux sysfs)
    if sys.platform == "linux":
        amd_devices = _detect_amd_rocm()
        devices.extend(amd_devices)

    # Strategy 3: Windows (WMI + DX diagnostics) — detects all vendors
    if sys.platform == "win32":
        wmi_devices = _detect_windows_wmi()
        devices = _merge_device_lists(devices, wmi_devices)

    # Strategy 4: macOS (system_profiler)
    if sys.platform == "darwin":
        mac_devices = _detect_macos()
        devices = _merge_device_lists(devices, mac_devices)

    # Strategy 5: Intel on Linux (sysfs)
    if sys.platform == "linux":
        intel_devices = _detect_intel_linux()
        devices.extend(intel_devices)

    # Strategy 6: Docker daemon (containerised fallback)
    # When running inside a container, host GPUs aren't directly
    # visible.  Query the Docker daemon (via the bind-mounted socket)
    # for registered GPU runtimes.
    if not devices:
        docker_devices = _detect_via_docker()
        devices.extend(docker_devices)

    if not devices:
        logger.info("No GPUs detected on this host")
        return GpuInventory()

    # Re-index devices sequentially
    reindexed = [
        GpuDevice(
            index=i,
            vendor=d.vendor,
            model=d.model,
            vram_bytes=d.vram_bytes,
            driver_version=d.driver_version,
            compute_api=d.compute_api,
        )
        for i, d in enumerate(devices)
    ]

    return _build_inventory(reindexed)


def redetect_gpus() -> GpuInventory:
    """Clear the detection cache and re-run GPU discovery.

    Useful after hardware state changes such as enabling a discrete GPU
    from power-saving mode or installing GPU drivers.
    """
    detect_gpus.cache_clear()
    return detect_gpus()


# ── Strategy 1: NVIDIA pynvml ─────────────────────────────────────────


def _detect_nvidia() -> list[GpuDevice]:
    """Detect NVIDIA GPUs using pynvml, falling back to nvidia-smi CLI."""
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

    # Fallback: nvidia-smi CLI (pynvml unavailable or failed)
    devices = _detect_nvidia_smi()
    if devices:
        logger.info("Detected %d NVIDIA GPU(s) via nvidia-smi CLI", len(devices))
    return devices


def _detect_nvidia_smi() -> list[GpuDevice]:
    """Detect NVIDIA GPUs by parsing nvidia-smi CSV output."""
    import shutil  # noqa: PLC0415

    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return []

    devices: list[GpuDevice] = []
    with contextlib.suppress(Exception):
        proc = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=index,name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return devices

        driver_version = ""
        for line in proc.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:  # noqa: PLR2004
                continue
            idx = 0
            with contextlib.suppress(ValueError):
                idx = int(parts[0])
            name = parts[1]
            vram_mib = 0
            with contextlib.suppress(ValueError):
                vram_mib = int(float(parts[2]))
            driver_version = parts[3]
            devices.append(
                GpuDevice(
                    index=idx,
                    vendor=GpuVendor.NVIDIA,
                    model=name,
                    vram_bytes=vram_mib * 1024 * 1024,
                    driver_version=driver_version,
                    compute_api=GpuComputeApi.CUDA,
                ),
            )

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


# ── Strategy 6: Docker daemon (containerised fallback) ────────────────


def _detect_via_docker() -> list[GpuDevice]:
    """Detect host GPUs by running an ``nvidia-smi`` probe container.

    When the backend runs inside a container, host GPUs are invisible to
    pynvml/sysfs.  The Docker socket (``/var/run/docker.sock``) is
    bind-mounted, so we:

    1. Check ``GET /info`` for the ``nvidia`` runtime.
    2. Create a tiny ``nvidia/cuda:base`` probe container **with GPU
       passthrough** and run ``nvidia-smi``.
    3. Parse the output for GPU model names and VRAM.
    4. If the probe fails (no adapters, no driver) → no GPU reported.

    The nvidia runtime being registered does **not** imply actual NVIDIA
    hardware is present (the toolkit can be installed on an AMD-only
    machine), so the probe is the only reliable check.
    """
    devices: list[GpuDevice] = []
    docker_sock = Path("/var/run/docker.sock")
    if not docker_sock.exists():
        logger.debug("Docker socket not found at %s — skipping Docker GPU probe", docker_sock)
        return devices

    try:
        import http.client  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        import socket as _socket  # noqa: PLC0415
        import time as _time  # noqa: PLC0415
        import urllib.parse as _url  # noqa: PLC0415

        class _UnixConn(http.client.HTTPConnection):
            """HTTP connection over a Unix domain socket."""

            def __init__(self, sock_path: str) -> None:
                super().__init__("localhost", timeout=10)
                self._sock_path = sock_path

            def connect(self) -> None:
                self.sock = _socket.socket(
                    _socket.AF_UNIX, _socket.SOCK_STREAM,
                )
                self.sock.settimeout(self.timeout)
                self.sock.connect(self._sock_path)

        sock_str = str(docker_sock)

        # ── 1. Check nvidia runtime is registered ────────────────────
        c1 = _UnixConn(sock_str)
        c1.request("GET", "/info")
        r1 = c1.getresponse()
        if r1.status != 200:
            c1.close()
            return devices
        info = _json.loads(r1.read())
        c1.close()

        runtimes = info.get("Runtimes") or {}
        default_runtime = info.get("DefaultRuntime", "")
        # Check for nvidia runtime or CDI device support (DGX Spark / newer toolkit)
        has_nvidia_runtime = (
            "nvidia" in runtimes
            or default_runtime == "nvidia"
            or any("nvidia" in str(v) for v in (info.get("SecurityOptions") or []))
        )
        if not has_nvidia_runtime:
            logger.debug(
                "No nvidia runtime found in Docker info. "
                "Runtimes=%s DefaultRuntime=%s",
                list(runtimes.keys()),
                default_runtime,
            )
            return devices

        logger.debug("nvidia runtime found — running GPU probe container")

        # ── 2. Create probe container ────────────────────────────────
        # Use a slim CUDA base image that ships nvidia-smi.
        # Try multiple images in case only one is available.
        probe_images = [
            "nvidia/cuda:12.6.3-base-ubuntu24.04",
            "nvidia/cuda:12.4.1-base-ubuntu22.04",
            "nvidia/cuda:12.0.0-base-ubuntu22.04",
            "ubuntu:22.04",  # nvidia-smi is mounted by the runtime
        ]
        probe_cmd = [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ]

        # Find first available image
        probe_image = None
        for img in probe_images:
            ci = _UnixConn(sock_str)
            ci.request("GET", f"/images/{img}/json")
            ri = ci.getresponse()
            ri.read()
            ci.close()
            if ri.status == 200:
                probe_image = img
                break

        if not probe_image:
            logger.warning(
                "Docker GPU probe: no suitable image found locally. "
                "Pull one of: %s",
                ", ".join(probe_images[:2]),
            )
            return devices

        logger.debug("Docker GPU probe using image: %s", probe_image)
        create_body = _json.dumps({
            "Image": probe_image,
            "Cmd": probe_cmd,
            "HostConfig": {
                "DeviceRequests": [{
                    "Driver": "",
                    "Count": -1,  # all GPUs
                    "Capabilities": [["gpu"]],
                }],
            },
        })

        c2 = _UnixConn(sock_str)
        c2.request(
            "POST", "/containers/create?name=llm-port-gpu-probe",
            body=create_body,
            headers={"Content-Type": "application/json"},
        )
        r2 = c2.getresponse()
        r2_body = _json.loads(r2.read())
        c2.close()

        if r2.status not in (200, 201):
            logger.debug("GPU probe container creation failed: %s", r2_body)
            return devices

        cid = r2_body["Id"]

        # ── 3. Start → wait → read logs → remove ────────────────────
        try:
            c3 = _UnixConn(sock_str)
            c3.request("POST", f"/containers/{cid}/start")
            r3 = c3.getresponse()
            r3.read()
            c3.close()

            if r3.status not in (200, 204):
                logger.debug("GPU probe container failed to start (rc=%d)", r3.status)
                return devices

            # Wait for the container (max 10 s)
            c4 = _UnixConn(sock_str)
            c4.request("POST", f"/containers/{cid}/wait?condition=not-running")
            c4.sock.settimeout(15)
            r4 = c4.getresponse()
            r4.read()
            c4.close()

            # Read logs (stdout only)
            c5 = _UnixConn(sock_str)
            c5.request("GET", f"/containers/{cid}/logs?stdout=true&stderr=false")
            r5 = c5.getresponse()
            raw_logs = r5.read()
            c5.close()

            # Docker multiplexed stream: 8-byte header per frame.
            # Strip headers to get plain text.
            output_parts: list[str] = []
            pos = 0
            while pos + 8 <= len(raw_logs):
                frame_size = int.from_bytes(raw_logs[pos + 4 : pos + 8], "big")
                frame_data = raw_logs[pos + 8 : pos + 8 + frame_size]
                output_parts.append(frame_data.decode("utf-8", errors="replace"))
                pos += 8 + frame_size

            output = "".join(output_parts).strip()
            if not output:
                return devices

            # Parse CSV: "NVIDIA GeForce RTX 4090, 24564"
            for idx, line in enumerate(output.splitlines()):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    continue
                name = parts[0]
                vram_mib = 0
                with contextlib.suppress(ValueError):
                    vram_mib = int(parts[1])
                devices.append(
                    GpuDevice(
                        index=idx,
                        vendor=GpuVendor.NVIDIA,
                        model=name,
                        vram_bytes=vram_mib * 1024 * 1024,
                        driver_version="",
                        compute_api=GpuComputeApi.CUDA,
                    ),
                )

            if devices:
                logger.info(
                    "Detected %d NVIDIA GPU(s) via Docker probe: %s",
                    len(devices),
                    ", ".join(d.model for d in devices),
                )
        finally:
            # Always clean up the probe container
            with contextlib.suppress(Exception):
                cx = _UnixConn(sock_str)
                cx.request("DELETE", f"/containers/{cid}?force=true")
                cx.getresponse().read()
                cx.close()
    except Exception:
        logger.warning("Docker GPU probe failed", exc_info=True)

    return devices


# ── Internal helpers ──────────────────────────────────────────────────

# Priority order for choosing the "primary" vendor in multi-GPU systems.
# Discrete compute GPUs (NVIDIA CUDA, AMD ROCm) outrank integrated.
_VENDOR_PRIORITY: dict[GpuVendor, int] = {
    GpuVendor.NVIDIA: 4,
    GpuVendor.AMD: 3,
    GpuVendor.INTEL: 2,
    GpuVendor.APPLE: 1,
    GpuVendor.UNKNOWN: 0,
}


def _merge_device_lists(
    existing: list[GpuDevice],
    new: list[GpuDevice],
) -> list[GpuDevice]:
    """Merge *new* devices into *existing*, skipping duplicates.

    Two devices are considered duplicates when they share the same
    vendor and their model names overlap (case-insensitive substring
    match).  When a duplicate is found the entry with **more VRAM info**
    (typically from a vendor-specific tool like pynvml) is kept.
    """
    merged = list(existing)
    for nd in new:
        nd_model = nd.model.lower().strip()
        duplicate = False
        for i, ed in enumerate(merged):
            ed_model = ed.model.lower().strip()
            if nd.vendor != ed.vendor:
                continue
            # Fuzzy model-name match: one name is a substring of the other
            if nd_model in ed_model or ed_model in nd_model:
                duplicate = True
                # Keep whichever has richer data (prefer pynvml over WMI)
                if nd.vram_bytes > ed.vram_bytes:
                    merged[i] = nd
                break
        if not duplicate:
            merged.append(nd)
    return merged


def _build_inventory(devices: list[GpuDevice]) -> GpuInventory:
    """Build a :class:`GpuInventory` from a list of devices.

    When multiple vendors are present the *primary* vendor is the one
    with the highest priority (discrete compute GPUs preferred).
    """
    if not devices:
        return GpuInventory()

    best = max(devices, key=lambda d: (_VENDOR_PRIORITY.get(d.vendor, 0), d.vram_bytes))
    return GpuInventory(
        devices=devices,
        primary_vendor=best.vendor,
        primary_compute_api=best.compute_api,
    )
