"""CLI entrypoint for llm_port_node_agent."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from llm_port_node_agent.config import AgentConfig
from llm_port_node_agent.service import NodeAgentService

SERVICE_NAME = "llmport-agent"
_IS_WINDOWS = platform.system() == "Windows"


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


async def _run() -> None:
    config = AgentConfig.from_env()
    _configure_logging(config.log_level)
    service = NodeAgentService(config)
    try:
        await service.run_forever()
    finally:
        await service.close()


# ── Helpers (shared) ──────────────────────────────────────────────


def _run_cmd(cmd: list[str], *, check: bool = True, quiet: bool = False) -> int:
    kwargs: dict = {}
    if quiet:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    result = subprocess.run(cmd, **kwargs)  # noqa: S603
    if result.returncode != 0 and check:
        print(f"ERROR: {' '.join(cmd)}", file=sys.stderr)
        if quiet and getattr(result, "stderr", None):
            print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


def _collect_env_lines() -> list[str]:
    """Collect current LLM_PORT_NODE_AGENT_* env vars as KEY=VALUE lines."""
    return [
        f"{k}={v}"
        for k, v in sorted(os.environ.items())
        if k.startswith("LLM_PORT_NODE_AGENT_")
    ]


# ── Linux / systemd helpers ──────────────────────────────────────


def _require_linux() -> None:
    if _IS_WINDOWS:
        print("ERROR: This code path requires Linux with systemd.", file=sys.stderr)
        sys.exit(1)
    if not shutil.which("systemctl"):
        print("ERROR: systemctl not found — systemd is required.", file=sys.stderr)
        sys.exit(1)


def _sudo_prefix() -> list[str]:
    if os.getuid() == 0:  # type: ignore[attr-defined]
        return []
    probe = subprocess.run(  # noqa: S603
        ["sudo", "-n", "true"], capture_output=True,
    )
    if probe.returncode != 0:
        print("sudo access required. You may be prompted for your password.")
    return ["sudo"]


def _find_service_template() -> Path | None:
    candidates = [
        Path(__file__).resolve().parent.parent / "deploy" / "systemd" / f"{SERVICE_NAME}.service",
        Path(sys.prefix) / "share" / SERVICE_NAME / f"{SERVICE_NAME}.service",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _build_service_content(agent_bin: str) -> str:
    template = _find_service_template()
    if template:
        content = template.read_text(encoding="utf-8")
        return re.sub(
            r"^ExecStart=.*$",
            f"ExecStart={agent_bin}",
            content,
            flags=re.MULTILINE,
        )
    return (
        "[Unit]\n"
        "Description=llm-port node agent\n"
        "After=network-online.target docker.service\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"EnvironmentFile=-/etc/{SERVICE_NAME}.env\n"
        f"ExecStart={agent_bin}\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


# ── Windows helpers ───────────────────────────────────────────────

_WIN_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "llmport-agent"
_WIN_ENV_FILE = _WIN_DATA_DIR / "agent.env"
_WIN_PID_FILE = _WIN_DATA_DIR / "agent.pid"
_WIN_LOG_FILE = _WIN_DATA_DIR / "agent.log"
_WIN_WRAPPER = _WIN_DATA_DIR / "run-agent.cmd"
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WIN_RUN_VALUE = "llmport-agent"


def _write_win_env_file(env_lines: list[str]) -> None:
    """Write env vars to a file that the wrapper script will source."""
    _WIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _WIN_ENV_FILE.write_text("\n".join(env_lines) + "\n", encoding="utf-8")


def _write_win_wrapper(agent_bin: str) -> None:
    """Write a .cmd wrapper that loads env vars then runs the agent."""
    lines = [
        "@echo off",
        f'for /f "usebackq tokens=1,* delims==" %%A in ("{_WIN_ENV_FILE}") do set "%%A=%%B"',
        f'"{agent_bin}"',
    ]
    _WIN_WRAPPER.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def _win_read_pid() -> int | None:
    """Read agent PID from the PID file; return None if not found/stale."""
    if not _WIN_PID_FILE.exists():
        return None
    try:
        pid = int(_WIN_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    # Verify it's still running
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)  # type: ignore[union-attr]
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[union-attr]
        return pid
    return None


def _win_add_autostart() -> None:
    """Add a Run registry key so the agent starts on logon."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _WIN_RUN_VALUE, 0, winreg.REG_SZ, str(_WIN_WRAPPER))
    except OSError:
        pass  # non-critical


def _win_remove_autostart() -> None:
    """Remove the Run registry key."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _WIN_RUN_VALUE)
    except FileNotFoundError:
        pass
    except OSError:
        pass


# ── Subcommands ───────────────────────────────────────────────────


def cmd_start() -> None:
    """Install and start llmport-agent as a background service."""
    env_lines = _collect_env_lines()
    if not any(line.startswith("LLM_PORT_NODE_AGENT_BACKEND_URL=") for line in env_lines):
        print("ERROR: LLM_PORT_NODE_AGENT_BACKEND_URL must be set.", file=sys.stderr)
        sys.exit(1)

    agent_bin = shutil.which("llmport-agent")
    if not agent_bin:
        print("ERROR: llmport-agent not found on PATH.", file=sys.stderr)
        sys.exit(1)

    if _IS_WINDOWS:
        _cmd_start_windows(agent_bin, env_lines)
    else:
        _cmd_start_linux(agent_bin, env_lines)


def _cmd_start_linux(agent_bin: str, env_lines: list[str]) -> None:
    _require_linux()
    sudo = _sudo_prefix()

    service_content = _build_service_content(agent_bin)
    env_content = "\n".join(env_lines) + "\n"

    tmp_svc = tmp_env = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".service") as sf:
            sf.write(service_content)
            tmp_svc = sf.name
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as ef:
            ef.write(env_content)
            tmp_env = ef.name

        if _run_cmd([*sudo, "install", "-m", "0644", tmp_svc, f"/etc/systemd/system/{SERVICE_NAME}.service"]) != 0:
            sys.exit(1)
        if _run_cmd([*sudo, "install", "-m", "0600", tmp_env, f"/etc/{SERVICE_NAME}.env"]) != 0:
            sys.exit(1)
        _run_cmd([*sudo, "mkdir", "-p", "/var/lib/llmport-agent"], check=False, quiet=True)
        if _run_cmd([*sudo, "systemctl", "daemon-reload"]) != 0:
            sys.exit(1)
        if _run_cmd([*sudo, "systemctl", "enable", "--now", SERVICE_NAME]) != 0:
            sys.exit(1)
    finally:
        if tmp_svc:
            os.unlink(tmp_svc)
        if tmp_env:
            os.unlink(tmp_env)

    print(f"{SERVICE_NAME} service installed and started.")
    print(f"  View logs:  journalctl -u {SERVICE_NAME} -f")
    print(f"  Stop:       llmport-agent stop")


def _cmd_start_windows(agent_bin: str, env_lines: list[str]) -> None:
    existing_pid = _win_read_pid()
    if existing_pid is not None:
        print(f"{SERVICE_NAME} is already running (PID {existing_pid}).")
        sys.exit(0)

    # Write env file + wrapper script
    _write_win_env_file(env_lines)
    _write_win_wrapper(agent_bin)

    # Launch the wrapper as a detached background process
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008
    log_fd = open(_WIN_LOG_FILE, "a", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(  # noqa: S603
        [str(_WIN_WRAPPER)],
        stdout=log_fd,
        stderr=log_fd,
        creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
    )
    _WIN_PID_FILE.write_text(str(proc.pid))

    # Register autostart on logon
    _win_add_autostart()

    print(f"{SERVICE_NAME} started (PID {proc.pid}).")
    print(f"  Config: {_WIN_ENV_FILE}")
    print(f"  Logs:   {_WIN_LOG_FILE}")
    print(f"  Stop:   llmport-agent stop")


def cmd_stop() -> None:
    """Stop and remove the llmport-agent background service."""
    if _IS_WINDOWS:
        _cmd_stop_windows()
    else:
        _cmd_stop_linux()


def _cmd_stop_linux() -> None:
    _require_linux()
    sudo = _sudo_prefix()
    _run_cmd([*sudo, "systemctl", "disable", "--now", SERVICE_NAME], check=False)
    print(f"{SERVICE_NAME} service stopped and disabled.")


def _cmd_stop_windows() -> None:
    pid = _win_read_pid()
    if pid is not None:
        _run_cmd(["taskkill", "/F", "/PID", str(pid)], check=False, quiet=True)
    # Also try by name in case PID file is stale
    _run_cmd(["taskkill", "/F", "/IM", "llmport-agent.exe"], check=False, quiet=True)
    if _WIN_PID_FILE.exists():
        _WIN_PID_FILE.unlink(missing_ok=True)
    _win_remove_autostart()
    print(f"{SERVICE_NAME} stopped.")


def cmd_status() -> None:
    """Show service status."""
    if _IS_WINDOWS:
        _cmd_status_windows()
    else:
        _cmd_status_linux()


def _cmd_status_linux() -> None:
    _require_linux()
    os.execlp("systemctl", "systemctl", "status", SERVICE_NAME)


def _cmd_status_windows() -> None:
    pid = _win_read_pid()
    if pid is not None:
        print(f"{SERVICE_NAME} is running (PID {pid}).")
        print(f"  Config: {_WIN_ENV_FILE}")
        print(f"  Logs:   {_WIN_LOG_FILE}")
    else:
        print(f"{SERVICE_NAME} is not running.")


# ── Main ──────────────────────────────────────────────────────────


def main() -> None:
    """Process entrypoint."""
    parser = argparse.ArgumentParser(
        prog="llmport-agent",
        description="llm-port node agent — host-side execution bridge.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", help="Install and start as a background service")
    sub.add_parser("stop", help="Stop and remove the background service")
    sub.add_parser("status", help="Show background service status")

    args = parser.parse_args()

    if args.command is None:
        asyncio.run(_run())
    elif args.command == "start":
        cmd_start()
    elif args.command == "stop":
        cmd_stop()
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
