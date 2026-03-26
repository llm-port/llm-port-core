"""OS-level package and firmware update support.

Profile-driven: reads *update_config* from the assigned node profile to
decide which package manager command to use (``upgrade`` vs
``dist-upgrade``), whether firmware checks via ``fwupdmgr`` are enabled,
and the reboot policy.

Two independent update scopes are exposed to the frontend:

* **system** – OS packages via the detected package manager
* **firmware** – device firmware via ``fwupdmgr`` (Linux only)

Progress is streamed via the standard ``emit_progress`` callback.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import shutil
import sys
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

ProgressEmitter = Callable[[dict[str, Any]], Awaitable[None]]


# ── helpers ─────────────────────────────────────────────────────


def _detect_package_manager() -> str | None:
    """Return the name of the best available package manager."""
    if sys.platform == "darwin":
        return "brew" if shutil.which("brew") else None
    if sys.platform == "win32":
        return "winget"
    for mgr in ("apt", "dnf", "yum"):
        if shutil.which(mgr):
            return mgr
    return None


def _has_fwupdmgr() -> bool:
    return sys.platform == "linux" and shutil.which("fwupdmgr") is not None


async def _run(cmd: list[str], *, timeout: int = 300) -> tuple[int, str]:
    """Run a subprocess and return (returncode, combined output)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "Command timed out"
    return proc.returncode or 0, (stdout or b"").decode("utf-8", errors="replace")


# ── parsers ─────────────────────────────────────────────────────


def _parse_apt_upgradable(output: str) -> list[dict[str, str]]:
    """Parse ``apt list --upgradable``.

    Example line::

        bind9-dnsutils/jammy-updates 1:9.18.28 amd64 [upgradable from: 1:9.18.24]
    """
    packages: list[dict[str, str]] = []
    for line in output.splitlines():
        if "/" not in line or "Listing" in line:
            continue
        name = line.split("/")[0].strip()
        parts = line.split()
        new_version = parts[1] if len(parts) >= 2 else ""
        m = re.search(r"\[upgradable from:\s*(.+?)\]", line)
        old_version = m.group(1).strip() if m else ""
        if name:
            packages.append({
                "name": name,
                "old_version": old_version,
                "new_version": new_version,
            })
    return packages


def _parse_dnf_check_update(output: str) -> list[dict[str, str]]:
    """Parse ``dnf check-update`` output."""
    packages: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "." in parts[0]:
            packages.append({
                "name": parts[0],
                "old_version": "",
                "new_version": parts[1],
            })
    return packages


def _parse_brew_outdated(output: str) -> list[dict[str, str]]:
    """Parse ``brew outdated --verbose``.

    Format: ``package (old_version) < new_version``
    """
    packages: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\S+)\s+\((.+?)\)\s*<\s*(.+)$", line)
        if m:
            packages.append({
                "name": m.group(1),
                "old_version": m.group(2).strip(),
                "new_version": m.group(3).strip(),
            })
        else:
            packages.append({"name": line, "old_version": "", "new_version": ""})
    return packages


def _parse_fwupd_updates(output: str) -> list[dict[str, str]]:
    """Parse ``fwupdmgr get-updates`` text output.

    Device blocks look like::

        BMC Firmware
          Device ID:       xxxx
          Current version: 1.2.3
          Update Version:  1.2.4
    """
    devices: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Device name = non-indented line without a colon key-value
        if not line.startswith((" ", "\t")) and ":" not in stripped:
            if current and current.get("name"):
                devices.append(current)
            current = {"name": stripped}
        elif current is not None:
            low = stripped.lower()
            if low.startswith("current version:"):
                current["current_version"] = stripped.split(":", 1)[1].strip()
            elif low.startswith("update version:"):
                current["update_version"] = stripped.split(":", 1)[1].strip()
            elif low.startswith("summary:"):
                current["summary"] = stripped.split(":", 1)[1].strip()

    if current and current.get("name"):
        devices.append(current)
    return devices


# ── check ───────────────────────────────────────────────────────


async def check_updates(
    emit_progress: ProgressEmitter,
    *,
    update_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check for available system packages **and** firmware updates.

    Returns two separate lists driven by the profile's *update_config*.
    """
    cfg = update_config or {}
    firmware_enabled = cfg.get("firmware_enabled", _has_fwupdmgr())

    mgr = cfg.get("package_manager", "auto")
    if mgr == "auto":
        mgr = _detect_package_manager()

    result: dict[str, Any] = {
        "package_manager": mgr,
        "os": platform.system(),
        "system_packages": {
            "updates_available": False,
            "package_count": 0,
            "packages": [],
        },
        "firmware": {
            "available": False,
            "updates_available": False,
            "device_count": 0,
            "devices": [],
        },
    }

    # ── system packages ──
    if mgr:
        await emit_progress({
            "phase": "check_updates",
            "message": f"Refreshing package index via {mgr}…",
        })

        packages: list[dict[str, str]] = []
        if mgr == "apt":
            await _run(["apt", "update", "-qq"], timeout=120)
            code, output = await _run(["apt", "list", "--upgradable"], timeout=60)
            packages = _parse_apt_upgradable(output) if code == 0 else []
        elif mgr in ("dnf", "yum"):
            code, output = await _run([mgr, "check-update", "-q"], timeout=120)
            packages = _parse_dnf_check_update(output) if code in (0, 100) else []
        elif mgr == "brew":
            await _run(["brew", "update", "--quiet"], timeout=120)
            code, output = await _run(["brew", "outdated", "--verbose"], timeout=60)
            packages = _parse_brew_outdated(output) if code == 0 else []
        elif mgr == "winget":
            code, output = await _run(
                ["winget", "upgrade", "--include-unknown"], timeout=120,
            )
            packages = [
                {"name": line.strip(), "old_version": "", "new_version": ""}
                for line in output.splitlines() if line.strip()
            ] if code == 0 else []

        await emit_progress({
            "phase": "check_updates",
            "message": f"Found {len(packages)} system package update(s)",
        })
        result["system_packages"] = {
            "updates_available": len(packages) > 0,
            "package_count": len(packages),
            "packages": packages[:200],
        }
    else:
        await emit_progress({
            "phase": "check_updates",
            "message": "No supported package manager found",
        })

    # ── firmware ──
    if firmware_enabled and _has_fwupdmgr():
        await emit_progress({
            "phase": "check_firmware",
            "message": "Refreshing firmware metadata…",
        })
        await _run(["fwupdmgr", "refresh", "--force"], timeout=120)

        await emit_progress({
            "phase": "check_firmware",
            "message": "Checking for firmware updates…",
        })
        code, output = await _run(["fwupdmgr", "get-updates"], timeout=60)
        devices = _parse_fwupd_updates(output) if code == 0 else []

        await emit_progress({
            "phase": "check_firmware",
            "message": f"Found {len(devices)} firmware update(s)",
        })
        result["firmware"] = {
            "available": True,
            "updates_available": len(devices) > 0,
            "device_count": len(devices),
            "devices": devices[:50],
        }
    elif firmware_enabled:
        result["firmware"]["available"] = False

    return result


# ── apply ───────────────────────────────────────────────────────


async def apply_updates(
    emit_progress: ProgressEmitter,
    *,
    scope: str = "all",
    update_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply pending updates.

    Parameters
    ----------
    scope:
        ``"all"`` – system packages + firmware (default);
        ``"system"`` – system packages only;
        ``"firmware"`` – firmware only.
    update_config:
        Profile's update_config dict.
    """
    cfg = update_config or {}
    upgrade_command = cfg.get("upgrade_command", "upgrade")
    reboot_policy = cfg.get("reboot_policy", "prompt")

    mgr = cfg.get("package_manager", "auto")
    if mgr == "auto":
        mgr = _detect_package_manager()

    result: dict[str, Any] = {
        "package_manager": mgr,
        "scope": scope,
        "system_update": None,
        "firmware_update": None,
        "reboot_required": False,
        "reboot_policy": reboot_policy,
    }

    # ── system packages ──
    if scope in ("all", "system") and mgr:
        await emit_progress({
            "phase": "apply_system",
            "message": f"Applying system updates via {mgr} {upgrade_command}…",
        })

        if mgr == "apt":
            await _run(["apt", "update", "-qq"], timeout=120)
            code, output = await _run(
                ["apt", upgrade_command, "-y"], timeout=900,
            )
        elif mgr in ("dnf", "yum"):
            code, output = await _run(
                [mgr, "upgrade", "-y", "--quiet"], timeout=900,
            )
        elif mgr == "brew":
            code, output = await _run(
                ["brew", "upgrade", "--quiet"], timeout=600,
            )
        elif mgr == "winget":
            code, output = await _run(
                ["winget", "upgrade", "--all", "--accept-source-agreements",
                 "--accept-package-agreements"], timeout=600,
            )
        else:
            code, output = 1, "Unsupported package manager"

        success = code == 0
        await emit_progress({
            "phase": "apply_system",
            "message": "System packages updated" if success else f"System update failed (exit {code})",
        })
        result["system_update"] = {
            "success": success,
            "exit_code": code,
            "output_tail": output[-2000:] if output else "",
        }

    # ── firmware ──
    if scope in ("all", "firmware") and _has_fwupdmgr():
        await emit_progress({
            "phase": "apply_firmware",
            "message": "Refreshing firmware metadata…",
        })
        await _run(["fwupdmgr", "refresh", "--force"], timeout=120)

        await emit_progress({
            "phase": "apply_firmware",
            "message": "Applying firmware updates…",
        })
        code, output = await _run(
            ["fwupdmgr", "update", "-y", "--no-reboot-check"], timeout=600,
        )
        success = code == 0
        await emit_progress({
            "phase": "apply_firmware",
            "message": "Firmware updated" if success else f"Firmware update failed (exit {code})",
        })
        result["firmware_update"] = {
            "success": success,
            "exit_code": code,
            "output_tail": output[-2000:] if output else "",
        }

    # ── reboot check ──
    reboot_required = False
    if sys.platform == "linux":
        from pathlib import Path

        reboot_required = Path("/var/run/reboot-required").exists()

    result["reboot_required"] = reboot_required

    if reboot_required:
        await emit_progress({
            "phase": "reboot",
            "message": "System requires a reboot to complete updates",
        })
        if reboot_policy == "if_required":
            await emit_progress({
                "phase": "reboot",
                "message": "Rebooting system…",
            })
            await _run(["reboot"], timeout=10)
            result["rebooting"] = True

    return result
