"""Cross-platform prerequisite installer.

Supports Windows (winget / scoop), macOS (brew), and
Linux (apt / dnf / pacman / zypper).  Each tool declares
install commands per platform+manager pair.  Docker is
intentionally excluded — it requires admin, reboots, and
kernel-level setup that can't be safely automated.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from llmport.core.console import console, error, info, success, warning


# ── Types ─────────────────────────────────────────────────────────

@dataclass
class Prerequisite:
    """A required CLI tool with platform-specific install commands."""

    name: str
    check_cmd: str  # binary name on PATH
    required: bool = True  # False = optional (warn only)
    install_commands: dict[str, list[str]] = field(default_factory=dict)
    post_install_hint: str = ""


# ── Platform / package-manager detection ──────────────────────────

def _detect_platform_key() -> str:
    """Return a key like ``windows/winget``, ``darwin/brew``, ``linux/apt``."""
    system = platform.system().lower()

    if system == "windows":
        for mgr in ("winget", "scoop", "choco"):
            if shutil.which(mgr):
                return f"windows/{mgr}"
        return "windows/winget"  # fallback label

    if system == "darwin":
        if shutil.which("brew"):
            return "darwin/brew"
        return "darwin/brew"

    # Linux — detect package manager
    for mgr in ("apt", "dnf", "pacman", "zypper"):
        if shutil.which(mgr):
            return f"linux/{mgr}"
    return "linux/unknown"


# ── Tool definitions ─────────────────────────────────────────────

_UV = Prerequisite(
    name="uv",
    check_cmd="uv",
    install_commands={
        "windows/winget": ["powershell", "-NoProfile", "-Command",
                           "irm https://astral.sh/uv/install.ps1 | iex"],
        "windows/scoop": ["scoop", "install", "uv"],
        "windows/choco": ["choco", "install", "uv", "-y"],
        "darwin/brew": ["brew", "install", "uv"],
        "linux/apt": ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
        "linux/dnf": ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
        "linux/pacman": ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
        "linux/zypper": ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
    },
    post_install_hint="You may need to restart your shell for uv to be on PATH.",
)

_GIT = Prerequisite(
    name="git",
    check_cmd="git",
    install_commands={
        "windows/winget": ["winget", "install", "--id", "Git.Git",
                           "--source", "winget", "--accept-package-agreements",
                           "--accept-source-agreements"],
        "windows/scoop": ["scoop", "install", "git"],
        "windows/choco": ["choco", "install", "git", "-y"],
        "darwin/brew": ["brew", "install", "git"],
        "linux/apt": ["sudo", "apt", "install", "-y", "git"],
        "linux/dnf": ["sudo", "dnf", "install", "-y", "git"],
        "linux/pacman": ["sudo", "pacman", "-S", "--noconfirm", "git"],
        "linux/zypper": ["sudo", "zypper", "install", "-y", "git"],
    },
)

_NODE = Prerequisite(
    name="node",
    check_cmd="node",
    install_commands={
        "windows/winget": ["winget", "install", "--id", "OpenJS.NodeJS.LTS",
                           "--source", "winget", "--accept-package-agreements",
                           "--accept-source-agreements"],
        "windows/scoop": ["scoop", "install", "nodejs-lts"],
        "windows/choco": ["choco", "install", "nodejs-lts", "-y"],
        "darwin/brew": ["brew", "install", "node"],
        "linux/apt": ["sudo", "apt", "install", "-y", "nodejs", "npm"],
        "linux/dnf": ["sudo", "dnf", "install", "-y", "nodejs", "npm"],
        "linux/pacman": ["sudo", "pacman", "-S", "--noconfirm", "nodejs", "npm"],
        "linux/zypper": ["sudo", "zypper", "install", "-y", "nodejs", "npm"],
    },
    post_install_hint="You may need to restart your shell for node/npm to be on PATH.",
)

_DOCKER = Prerequisite(
    name="docker",
    check_cmd="docker",
    install_commands={},  # intentionally empty — manual only
    post_install_hint=(
        "Docker requires manual installation:\n"
        "  Windows / macOS: https://docs.docker.com/desktop/\n"
        "  Linux:           https://docs.docker.com/engine/install/"
    ),
)

DEV_PREREQUISITES: list[Prerequisite] = [_DOCKER, _GIT, _UV, _NODE]


# ── Check logic ──────────────────────────────────────────────────

@dataclass
class CheckResult:
    """Result of checking a single prerequisite."""

    prereq: Prerequisite
    found: bool
    version: str = ""
    installable: bool = False  # True if we have an install command for this platform


def check_prerequisites(
    prereqs: list[Prerequisite] | None = None,
) -> list[CheckResult]:
    """Check which prerequisites are present on the system."""
    from llmport.core.detect import check_tool  # noqa: PLC0415

    prereqs = prereqs or DEV_PREREQUISITES
    platform_key = _detect_platform_key()
    results: list[CheckResult] = []

    for p in prereqs:
        tc = check_tool(p.check_cmd)
        installable = bool(p.install_commands.get(platform_key))
        results.append(
            CheckResult(
                prereq=p,
                found=tc.found,
                version=tc.version,
                installable=installable,
            ),
        )
    return results


# ── Install logic ────────────────────────────────────────────────

def _run_install(cmd: list[str], name: str) -> bool:
    """Run an install command and return True on success."""
    console.print(f"  [cyan]Installing {name}…[/cyan]")
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            timeout=300,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        error(f"  Install command failed: {exc}")
        return False


def install_missing(
    results: list[CheckResult],
    *,
    auto_confirm: bool = False,
) -> list[CheckResult]:
    """Attempt to install missing prerequisites.

    Returns the subset that were successfully installed.
    """
    platform_key = _detect_platform_key()
    missing = [r for r in results if not r.found]

    if not missing:
        return []

    installable = [r for r in missing if r.installable]
    manual_only = [r for r in missing if not r.installable]

    if manual_only:
        for r in manual_only:
            warning(f"  {r.prereq.name}: cannot be auto-installed on this platform.")
            if r.prereq.post_install_hint:
                console.print(f"    [dim]{r.prereq.post_install_hint}[/dim]")

    if not installable:
        return []

    names = ", ".join(r.prereq.name for r in installable)
    if not auto_confirm:
        import click  # noqa: PLC0415

        if not click.confirm(f"Install missing tools ({names})?", default=True):
            info("Skipping prerequisite installation.")
            return []

    installed: list[CheckResult] = []
    for r in installable:
        cmd = r.prereq.install_commands[platform_key]
        if _run_install(cmd, r.prereq.name):
            # Re-check after install — the binary might not be on
            # the current PATH yet (e.g. uv installer adds to
            # ~/.local/bin which isn't in this session's PATH).
            from llmport.core.detect import check_tool  # noqa: PLC0415

            tc = check_tool(r.prereq.check_cmd)
            if tc.found:
                success(f"  {r.prereq.name} {tc.version} ✓")
                installed.append(r)
            else:
                warning(f"  {r.prereq.name} installed but not yet on PATH.")
                if r.prereq.post_install_hint:
                    console.print(f"    [dim]{r.prereq.post_install_hint}[/dim]")
                installed.append(r)  # still count as installed
        else:
            error(f"  Failed to install {r.prereq.name}.")
    return installed


# ── Convenience — run full check + optional install ──────────────

def ensure_prerequisites(
    *,
    install: bool = False,
    auto_confirm: bool = False,
) -> bool:
    """Check prerequisites and optionally install missing ones.

    Returns True if all required tools are available (or were installed).
    """
    results = check_prerequisites()

    has_missing = False
    for r in results:
        mark = "[green]✓[/green]" if r.found else "[red]✗[/red]"
        ver = f" {r.version}" if r.version else ""
        console.print(f"  {mark} {r.prereq.name}{ver}")
        if not r.found and r.prereq.required:
            has_missing = True

    if not has_missing:
        return True

    if install:
        installed = install_missing(results, auto_confirm=auto_confirm)
        # Re-check required tools
        remaining = check_prerequisites()
        still_missing = [r for r in remaining if not r.found and r.prereq.required]
        if not still_missing:
            return True
        for r in still_missing:
            error(f"  {r.prereq.name} is still missing.")
        return False

    return False
