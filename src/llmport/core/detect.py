"""System detection — OS, Docker, GPU, ports, disk, RAM.

Every detection function returns a typed result dataclass so callers
can render them however suits (Rich table, Textual widget, JSON, etc.).
All detection is synchronous and subprocess-based — no Docker SDK
dependency, just ``docker`` and ``nvidia-smi`` / ``rocm-smi`` on PATH.
"""

from __future__ import annotations

import platform
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import psutil


# ── Result types ──────────────────────────────────────────────────


@dataclass
class OSInfo:
    """Operating system details."""

    system: str  # "Linux", "Windows", "Darwin"
    release: str
    version: str
    machine: str  # "x86_64", "aarch64"

    @property
    def display(self) -> str:
        """Human-friendly OS string."""
        return f"{self.system} {self.release} ({self.machine})"


@dataclass
class DockerInfo:
    """Docker Engine + Compose availability."""

    installed: bool = False
    version: str = ""
    compose_installed: bool = False
    compose_version: str = ""
    daemon_running: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        """Docker, Compose v2, and daemon are all available."""
        return self.installed and self.compose_installed and self.daemon_running


@dataclass
class GpuDevice:
    """Single GPU device."""

    index: int
    name: str
    vram_mb: int
    driver_version: str
    cuda_version: str = ""


@dataclass
class GpuInfo:
    """GPU detection result."""

    vendor: str = "none"  # "nvidia", "amd", "none"
    devices: list[GpuDevice] = field(default_factory=list)
    driver_version: str = ""
    cuda_version: str = ""
    rocm_version: str = ""
    error: str = ""

    @property
    def has_gpu(self) -> bool:
        """At least one GPU detected."""
        return len(self.devices) > 0

    @property
    def total_vram_mb(self) -> int:
        """Sum of VRAM across all devices."""
        return sum(d.vram_mb for d in self.devices)

    @property
    def display(self) -> str:
        """Short human-friendly GPU summary."""
        if not self.has_gpu:
            return "None detected"
        count = len(self.devices)
        name = self.devices[0].name
        vram = self.total_vram_mb
        suffix = f" × {count}" if count > 1 else ""
        return f"{name}{suffix} · {vram} MB VRAM"


@dataclass
class DiskInfo:
    """Disk space for a given path."""

    path: str
    total_gb: float
    free_gb: float
    used_pct: float


@dataclass
class RamInfo:
    """System memory."""

    total_gb: float
    available_gb: float
    used_pct: float


@dataclass
class PortCheck:
    """Result of checking whether a port is in use."""

    port: int
    label: str
    in_use: bool


@dataclass
class ToolCheck:
    """Result of checking whether a CLI tool is on PATH."""

    name: str
    found: bool
    version: str = ""
    path: str = ""


@dataclass
class SystemReport:
    """Aggregated detection results."""

    os: OSInfo
    docker: DockerInfo
    gpu: GpuInfo
    ram: RamInfo
    disk: DiskInfo
    ports: list[PortCheck] = field(default_factory=list)
    tools: list[ToolCheck] = field(default_factory=list)

    @property
    def all_clear(self) -> bool:
        """No blocking issues found."""
        return self.docker.ok and not any(p.in_use for p in self.ports)


# ── Detection functions ───────────────────────────────────────────


def detect_os() -> OSInfo:
    """Detect the operating system."""
    return OSInfo(
        system=platform.system(),
        release=platform.release(),
        version=platform.version(),
        machine=platform.machine(),
    )


def _run(cmd: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, capturing output."""
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def detect_docker() -> DockerInfo:
    """Detect Docker Engine and Docker Compose v2."""
    info = DockerInfo()

    # Docker Engine
    docker_bin = shutil.which("docker")
    if not docker_bin:
        info.error = "docker not found on PATH"
        return info

    try:
        result = _run([docker_bin, "version", "--format", "{{.Server.Version}}"])
        if result.returncode == 0 and result.stdout.strip():
            info.installed = True
            info.version = result.stdout.strip()
        else:
            info.error = result.stderr.strip() or "docker version failed"
            return info
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        info.error = str(exc)
        return info

    # Docker daemon reachability
    try:
        result = _run([docker_bin, "info"])
        info.daemon_running = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        info.daemon_running = False

    # Docker Compose v2 (docker compose subcommand)
    try:
        result = _run([docker_bin, "compose", "version", "--short"])
        if result.returncode == 0 and result.stdout.strip():
            info.compose_installed = True
            info.compose_version = result.stdout.strip().lstrip("v")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return info


def detect_gpu() -> GpuInfo:
    """Detect GPU vendor and devices (NVIDIA via nvidia-smi, AMD via rocm-smi)."""
    info = GpuInfo()

    # Try NVIDIA first
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = _run([
                nvidia_smi,
                "--query-gpu=index,name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ])
            if result.returncode == 0 and result.stdout.strip():
                info.vendor = "nvidia"
                for line in result.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:  # noqa: PLR2004
                        device = GpuDevice(
                            index=int(parts[0]),
                            name=parts[1],
                            vram_mb=int(float(parts[2])),
                            driver_version=parts[3],
                        )
                        info.devices.append(device)
                        info.driver_version = parts[3]

                # Get CUDA version
                cuda_result = _run([nvidia_smi])
                if cuda_result.returncode == 0:
                    match = re.search(r"CUDA Version:\s*([\d.]+)", cuda_result.stdout)
                    if match:
                        info.cuda_version = match.group(1)
                        for d in info.devices:
                            d.cuda_version = match.group(1)
                return info
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            info.error = str(exc)

    # Try AMD ROCm
    rocm_smi = shutil.which("rocm-smi")
    if rocm_smi:
        try:
            result = _run([rocm_smi, "--showproductname", "--showmeminfo", "vram", "--csv"])
            if result.returncode == 0 and result.stdout.strip():
                info.vendor = "amd"
                # ROCm CSV parsing is more complex; simplified version
                for i, line in enumerate(result.stdout.strip().splitlines()[1:]):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2:  # noqa: PLR2004
                        info.devices.append(
                            GpuDevice(
                                index=i,
                                name=parts[1] if len(parts) > 1 else f"AMD GPU {i}",
                                vram_mb=0,  # parsed separately in real impl
                                driver_version="",
                            ),
                        )
                # ROCm version
                rocm_ver = _run(["rocm-smi", "--version"])
                if rocm_ver.returncode == 0:
                    info.rocm_version = rocm_ver.stdout.strip()
                return info
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            info.error = str(exc)

    return info


def detect_ram() -> RamInfo:
    """Detect system memory."""
    mem = psutil.virtual_memory()
    return RamInfo(
        total_gb=round(mem.total / (1024**3), 1),
        available_gb=round(mem.available / (1024**3), 1),
        used_pct=mem.percent,
    )


def detect_disk(path: str = "/") -> DiskInfo:
    """Detect disk space for the given path."""
    if platform.system() == "Windows":
        path = "C:\\"
    usage = psutil.disk_usage(path)
    return DiskInfo(
        path=path,
        total_gb=round(usage.total / (1024**3), 1),
        free_gb=round(usage.free / (1024**3), 1),
        used_pct=usage.percent,
    )


# ── Port map (all ports used by llm.port) ─────────────────────────

from llmport.core.registry import KNOWN_PORTS


def check_port(port: int, label: str = "") -> PortCheck:
    """Check if a TCP port is already in use on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        in_use = s.connect_ex(("127.0.0.1", port)) == 0
    return PortCheck(port=port, label=label, in_use=in_use)


def check_known_ports() -> list[PortCheck]:
    """Check all known llm.port ports."""
    return [check_port(port, label) for port, label in KNOWN_PORTS]


# ── Tool checks ───────────────────────────────────────────────────


def check_tool(name: str, *, version_flag: str = "--version") -> ToolCheck:
    """Check if a CLI tool exists on PATH and get its version."""
    path = shutil.which(name)
    if not path:
        return ToolCheck(name=name, found=False)
    try:
        result = _run([path, version_flag])
        version = result.stdout.strip() or result.stderr.strip()
        # Extract just the version number from common formats
        match = re.search(r"(\d+\.\d+[\.\d]*)", version)
        ver = match.group(1) if match else version[:60]
        return ToolCheck(name=name, found=True, version=ver, path=path)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ToolCheck(name=name, found=True, path=path)


def check_dev_tools() -> list[ToolCheck]:
    """Check all tools needed for dev mode."""
    return [
        check_tool("docker"),
        check_tool("git"),
        check_tool("uv"),
        check_tool("node"),
        check_tool("npm"),
        check_tool("poetry"),
    ]


# ── Full system report ────────────────────────────────────────────


def full_report(*, check_ports: bool = True, install_path: str = "/") -> SystemReport:
    """Run all detection checks and return a unified report."""
    return SystemReport(
        os=detect_os(),
        docker=detect_docker(),
        gpu=detect_gpu(),
        ram=detect_ram(),
        disk=detect_disk(install_path),
        ports=check_known_ports() if check_ports else [],
        tools=check_dev_tools(),
    )
