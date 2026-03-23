"""CLI entrypoint for llm_port_node_agent."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from llm_port_node_agent.config import AgentConfig
from llm_port_node_agent.service import NodeAgentService

SERVICE_NAME = "llmport-agent"
_IS_WINDOWS = platform.system() == "Windows"
_ENV_PREFIX = "LLM_PORT_NODE_AGENT_"

# ── Env-file paths (per-platform) ────────────────────────────────
_LINUX_ENV_FILE = Path(f"/etc/{SERVICE_NAME}.env")
_WIN_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "llmport-agent"
_WIN_ENV_FILE = _WIN_DATA_DIR / "agent.env"


def _env_file_path() -> Path:
    return _WIN_ENV_FILE if _IS_WINDOWS else _LINUX_ENV_FILE


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
        if k.startswith(_ENV_PREFIX)
    ]


def _load_env_file() -> dict[str, str]:
    """Load KEY=VALUE pairs from the platform env file (if it exists)."""
    path = _env_file_path()
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _save_env_file(env: dict[str, str]) -> Path:
    """Write env dict to the platform env file."""
    path = _env_file_path()
    lines = [f"{k}={v}" for k, v in sorted(env.items())]
    content = "\n".join(lines) + "\n"
    if _IS_WINDOWS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    else:
        # On Linux the env file lives in /etc — may need sudo
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            os.chmod(str(path), 0o600)
        except PermissionError:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
                f.write(content)
                tmp = f.name
            sudo = _sudo_prefix()
            _run_cmd([*sudo, "install", "-m", "0600", tmp, str(path)])
            os.unlink(tmp)
    return path


# ── Pretty printing ──────────────────────────────────────────────


_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RESET = "\033[0m"

# Disable ANSI on dumb terminals or redirected output
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    _BOLD = _DIM = _GREEN = _CYAN = _YELLOW = _RED = _RESET = ""


def _banner() -> None:
    print(f"\n{_BOLD}llmport-agent{_RESET} — llm-port node agent\n")


def _section(title: str) -> None:
    print(f"\n{_BOLD}{_CYAN}── {title} ───────────────────────────────{_RESET}\n")


def _kv(key: str, value: str, *, default: bool = False) -> None:
    tag = f" {_DIM}(default){_RESET}" if default else ""
    print(f"  {key:.<36s} {_GREEN}{value}{_RESET}{tag}")


def _warn(msg: str) -> None:
    print(f"  {_YELLOW}⚠  {msg}{_RESET}")


def _ok(msg: str) -> None:
    print(f"  {_GREEN}✓  {msg}{_RESET}")


def _err(msg: str) -> None:
    print(f"  {_RED}✗  {msg}{_RESET}")


# ── Show config ──────────────────────────────────────────────────


def _show_config() -> dict[str, str]:
    """Display current configuration from env file + env vars. Returns merged dict."""
    file_env = _load_env_file()
    live_env = {k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIX)}
    merged = {**file_env, **live_env}

    env_path = _env_file_path()

    _section("Current Configuration")

    if env_path.exists():
        _ok(f"Config file: {env_path}")
    else:
        _warn(f"No config file found at {env_path}")

    import socket
    hostname = socket.gethostname()

    fields = [
        ("BACKEND_URL", "http://127.0.0.1:8000"),
        ("AGENT_ID", hostname),
        ("HOST", hostname),
        ("ADVERTISE_HOST", ""),
        ("ADVERTISE_SCHEME", "http"),
        ("ENROLLMENT_TOKEN", ""),
        ("MODEL_STORE", "/srv/llm-port/models"),
        ("LOKI_URL", ""),
        ("LOG_LEVEL", "INFO"),
        ("VERIFY_TLS", "true"),
    ]

    for short_key, default in fields:
        full_key = f"{_ENV_PREFIX}{short_key}"
        val = merged.get(full_key, "")
        is_default = not val
        display_val = val or default or f"{_DIM}(not set){_RESET}"
        if short_key == "ENROLLMENT_TOKEN" and val:
            display_val = val[:8] + "…" + val[-4:] if len(val) > 16 else "***"
        _kv(short_key, display_val, default=is_default)

    # Check critical settings
    backend_url = merged.get(f"{_ENV_PREFIX}BACKEND_URL", "")
    loki_url = merged.get(f"{_ENV_PREFIX}LOKI_URL", "")

    print()
    if not backend_url:
        _warn("BACKEND_URL is not set — agent cannot connect to llm-port.")
    if not loki_url:
        _warn("LOKI_URL is not set — container log forwarding disabled.")

    return merged


# ── Interactive configure ────────────────────────────────────────


def _prompt(label: str, default: str = "", *, secret: bool = False) -> str:
    """Prompt user for a value with optional default."""
    if default:
        suffix = f" [{default}]: "
    else:
        suffix = ": "
    try:
        if secret:
            value = getpass.getpass(f"  {label}{suffix}")
        else:
            value = input(f"  {label}{suffix}")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value.strip() or default


def _prompt_yn(label: str, default: bool = True) -> bool:
    """Prompt yes/no."""
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {label} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not raw:
        return default
    return raw in {"y", "yes"}


# ── Settable config keys (short name → description + prompt label) ─────────
_SETTABLE_KEYS: dict[str, str] = {
    "BACKEND_URL": "LLM Port backend URL",
    "ENROLLMENT_TOKEN": "Enrollment token",
    "AGENT_ID": "Agent ID",
    "HOST": "Host identifier",
    "ADVERTISE_HOST": "Advertise host (IP/hostname reachable from LLM Port)",
    "ADVERTISE_SCHEME": "Advertise scheme (http/https)",
    "MODEL_STORE": "Model store path",
    "LOKI_URL": "Loki URL",
    "LOG_LEVEL": "Log level",
    "VERIFY_TLS": "Verify TLS certificates (true/false)",
    "IMAGE_ALLOWLIST": "Image allowlist (comma-separated prefixes)",
    "LOG_BATCH_SIZE": "Log batch size",
    "LOG_FLUSH_INTERVAL_SEC": "Log flush interval (seconds)",
    "HEARTBEAT_INTERVAL_SEC": "Heartbeat interval (seconds)",
    "RECONNECT_MIN_SEC": "Reconnect min backoff (seconds)",
    "RECONNECT_MAX_SEC": "Reconnect max backoff (seconds)",
    "REQUEST_TIMEOUT_SEC": "Request timeout (seconds)",
}


def cmd_show() -> None:
    """Display current configuration."""
    _banner()
    _show_config()


def cmd_configure_set(pairs: list[str]) -> None:
    """Set one or more config keys without the full wizard."""
    env = _load_env_file()
    changed: list[tuple[str, str]] = []

    for pair in pairs:
        if "=" not in pair:
            _err(f"Invalid format: {pair!r} — expected KEY=VALUE")
            sys.exit(1)
        raw_key, _, value = pair.partition("=")
        short_key = raw_key.strip().upper().removeprefix(_ENV_PREFIX)
        if short_key not in _SETTABLE_KEYS:
            _err(f"Unknown config key: {short_key}")
            _warn(f"Valid keys: {', '.join(sorted(_SETTABLE_KEYS))}")
            sys.exit(1)
        full_key = f"{_ENV_PREFIX}{short_key}"
        value = value.strip()
        if value:
            env[full_key] = value
        else:
            env.pop(full_key, None)
        changed.append((short_key, value or "(unset)"))

    saved = _save_env_file(env)
    for k, v in changed:
        display = v
        if k == "ENROLLMENT_TOKEN" and v and v != "(unset)":
            display = v[:8] + "…" + v[-4:] if len(v) > 16 else "***"
        _ok(f"{k} = {display}")
    _ok(f"Saved to {saved}")


def cmd_configure() -> None:
    """Interactive configuration wizard."""
    _banner()

    existing = _load_env_file()
    env: dict[str, str] = dict(existing)

    def _cur(short: str) -> str:
        return env.get(f"{_ENV_PREFIX}{short}", "")

    _section("Connection")

    backend_url = _prompt(
        "LLM Port backend URL",
        default=_cur("BACKEND_URL") or "http://127.0.0.1:8000",
    )
    env[f"{_ENV_PREFIX}BACKEND_URL"] = backend_url.rstrip("/")

    enrollment_token = _prompt(
        "Enrollment token (leave blank if not required)",
        default=_cur("ENROLLMENT_TOKEN"),
        secret=True,
    )
    if enrollment_token:
        env[f"{_ENV_PREFIX}ENROLLMENT_TOKEN"] = enrollment_token
    else:
        env.pop(f"{_ENV_PREFIX}ENROLLMENT_TOKEN", None)

    _section("Identity")

    import socket
    hostname = socket.gethostname()

    agent_id = _prompt("Agent ID", default=_cur("AGENT_ID") or hostname)
    env[f"{_ENV_PREFIX}AGENT_ID"] = agent_id

    host = _prompt("Host identifier", default=_cur("HOST") or hostname)
    env[f"{_ENV_PREFIX}HOST"] = host

    advertise_host = _prompt(
        "Advertise host (IP/hostname reachable from LLM Port)",
        default=_cur("ADVERTISE_HOST") or host,
    )
    env[f"{_ENV_PREFIX}ADVERTISE_HOST"] = advertise_host

    _section("Model Storage")

    default_store = "/srv/llm-port/models" if not _IS_WINDOWS else r"C:\llm-port\models"
    model_store = _prompt("Model store path", default=_cur("MODEL_STORE") or default_store)
    env[f"{_ENV_PREFIX}MODEL_STORE"] = model_store

    _section("Logging & Monitoring")

    # Determine the backend host for smart Loki default
    parsed_backend = urlparse(backend_url)
    backend_host = parsed_backend.hostname or "127.0.0.1"

    same_host = _prompt_yn(
        f"Is Loki running on the same host as LLM Port ({backend_host})?",
        default=True,
    )
    if same_host:
        loki_default = f"http://{backend_host}:3100"
    else:
        existing_loki = _cur("LOKI_URL")
        loki_default = existing_loki or ""

    loki_url = _prompt(
        "Loki URL (leave blank to disable log forwarding)",
        default=loki_default,
    )
    if loki_url:
        env[f"{_ENV_PREFIX}LOKI_URL"] = loki_url
    else:
        env.pop(f"{_ENV_PREFIX}LOKI_URL", None)

    log_level = _prompt("Log level", default=_cur("LOG_LEVEL") or "INFO")
    env[f"{_ENV_PREFIX}LOG_LEVEL"] = log_level.upper()

    _section("Security")

    verify_tls = _prompt_yn("Verify TLS certificates?", default=_cur("VERIFY_TLS") != "false")
    env[f"{_ENV_PREFIX}VERIFY_TLS"] = "true" if verify_tls else "false"

    # ── Review & save ─────────────────────────────────────────
    _section("Review")

    for key in sorted(env):
        short = key.removeprefix(_ENV_PREFIX)
        val = env[key]
        if short == "ENROLLMENT_TOKEN" and val:
            val = val[:8] + "…" + val[-4:] if len(val) > 16 else "***"
        _kv(short, val)

    print()
    if _prompt_yn("Save configuration?", default=True):
        saved = _save_env_file(env)
        _ok(f"Configuration saved to {saved}")
        print()

        # Also load into current process env so `start` can pick them up
        for k, v in env.items():
            os.environ[k] = v

        print(f"  Next steps:")
        print(f"    llmport-agent run     Run agent in foreground")
        print(f"    llmport-agent start   Install and start as system service")
    else:
        print("  Configuration not saved.")


# ── Interactive default (no command) ─────────────────────────────


def cmd_interactive() -> None:
    """Show config and offer choices when invoked without a subcommand."""
    _banner()
    _show_config()

    _section("What would you like to do?")
    print("  [1] Configure   — set up or change agent configuration")
    print("  [2] Run         — run agent in the foreground")
    print("  [3] Start       — install and start as a system service")
    print("  [4] Exit")
    print()

    try:
        choice = input(f"  {_BOLD}Select [1-4]:{_RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if choice == "1":
        cmd_configure()
    elif choice == "2":
        _load_env_into_process()
        asyncio.run(_run())
    elif choice == "3":
        _load_env_into_process()
        cmd_start()
    else:
        return


def _load_env_into_process() -> None:
    """Load saved env file into the current process environment."""
    file_env = _load_env_file()
    for k, v in file_env.items():
        if k not in os.environ:
            os.environ[k] = v


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
            f"ExecStart={agent_bin} run",
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
        f"ExecStart={agent_bin} run\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


# ── Windows helpers ───────────────────────────────────────────────

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
        f'"{agent_bin}" run',
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
    _load_env_into_process()
    env_lines = _collect_env_lines()

    if not any(line.startswith(f"{_ENV_PREFIX}BACKEND_URL=") for line in env_lines):
        _err("BACKEND_URL is not set. Run 'llmport-agent configure' first.")
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
        if _run_cmd([*sudo, "install", "-m", "0600", tmp_env, str(_LINUX_ENV_FILE)]) != 0:
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

    print(f"\n  {SERVICE_NAME} service installed and started.")
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


def cmd_run() -> None:
    """Run the agent in the foreground (load env file first)."""
    _load_env_into_process()
    asyncio.run(_run())


# ── Main ──────────────────────────────────────────────────────────


def main() -> None:
    """Process entrypoint."""
    parser = argparse.ArgumentParser(
        prog="llmport-agent",
        description="llm-port node agent — host-side execution bridge.",
    )
    sub = parser.add_subparsers(dest="command")
    p_configure = sub.add_parser("configure", help="Interactive configuration wizard (or --set KEY=VALUE)")
    p_configure.add_argument(
        "--set", "-s",
        dest="set_pairs",
        action="append",
        metavar="KEY=VALUE",
        help="Set a single config key (repeatable). E.g.: --set BACKEND_URL=http://host:8000",
    )
    sub.add_parser("show", help="Show current configuration")
    sub.add_parser("run", help="Run agent in the foreground")
    sub.add_parser("start", help="Install and start as a background service")
    sub.add_parser("stop", help="Stop and remove the background service")
    sub.add_parser("status", help="Show background service status")

    args = parser.parse_args()

    if args.command is None:
        cmd_interactive()
    elif args.command == "show":
        cmd_show()
    elif args.command == "configure":
        if args.set_pairs:
            cmd_configure_set(args.set_pairs)
        else:
            cmd_configure()
    elif args.command == "run":
        cmd_run()
    elif args.command == "start":
        cmd_start()
    elif args.command == "stop":
        cmd_stop()
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
