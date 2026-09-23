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
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

from llm_port_node_agent import __version__
from llm_port_node_agent.config import AgentConfig
from llm_port_node_agent.service import NodeAgentService
from llm_port_node_agent.single_instance import AlreadyRunningError, SingleInstanceLock

SERVICE_NAME = "llmport-agent"
#: Every name the agent runs under: the frozen binary, and the console
#: script a source install puts in a venv.
_AGENT_EXE_NAMES = frozenset({"llmport-agent", "llmport-agent.exe"})
#: Interpreters that run other programs. A shell is never the agent, and on
#: Windows psutil re-splits its long ``-c`` string into tokens that can land
#: an agent path next to the word "run" by accident.
_SHELL_NAMES = frozenset({
    "sh", "bash", "dash", "zsh", "ksh", "fish", "busybox",
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
})
_IS_WINDOWS = platform.system() == "Windows"
_ENV_PREFIX = "LLM_PORT_NODE_AGENT_"

# Someone has to walk to a browser and click, so wait in human time.
_JOIN_WAIT_SECONDS = 15 * 60
_JOIN_POLL_SECONDS = 3.0
# One version, declared in the package. This was a third literal copy.
_AGENT_VERSION = __version__

# ── Env-file paths (per-platform) ────────────────────────────────
_LINUX_SYSTEM_ENV_FILE = Path(f"/etc/{SERVICE_NAME}.env")
_LINUX_USER_ENV_FILE = Path.home() / ".config" / SERVICE_NAME / "agent.env"
_WIN_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "llmport-agent"
_WIN_ENV_FILE = _WIN_DATA_DIR / "agent.env"


def _env_file_path() -> Path:
    """Return the env-file path for saving.

    On Linux, prefer the system-wide ``/etc`` file when running as root or
    when it already exists (agent running as a service).  Otherwise fall
    back to ``~/.config/llmport-agent/agent.env`` for unprivileged users.
    """
    if _IS_WINDOWS:
        return _WIN_ENV_FILE
    if os.getuid() == 0:  # type: ignore[attr-defined]
        return _LINUX_SYSTEM_ENV_FILE
    if _LINUX_SYSTEM_ENV_FILE.exists() and os.access(_LINUX_SYSTEM_ENV_FILE, os.W_OK):
        return _LINUX_SYSTEM_ENV_FILE
    return _LINUX_USER_ENV_FILE


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # httpx logs every request at INFO. The log forwarder pushes to Loki every
    # few seconds, so each push wrote a line that the next push shipped to
    # Loki: the agent's logs filled with records of sending its logs.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLogger().level))


def _inject_env_file() -> None:
    """Load the env file and inject values into ``os.environ``.

    Values already present in the real environment take precedence,
    so the file acts as a set of defaults.
    """
    file_env = _effective_env()
    for key, value in file_env.items():
        if key not in os.environ:
            os.environ[key] = value


async def _run() -> None:
    _inject_env_file()
    config = AgentConfig.from_env()
    _configure_logging(config.log_level)

    # Refuse to be the second agent on this node.  Two of them enrol as the
    # same machine and hold two streams; the backend then dispatches commands
    # to one session while the other is the live one, and those commands never
    # reach a terminal state.  Nothing appears to happen, anywhere.
    lock = SingleInstanceLock(config.state_path)
    try:
        lock.acquire()
    except AlreadyRunningError as exc:
        _err(str(exc))
        sys.exit(1)

    service = NodeAgentService(config)
    try:
        await service.run_forever()
    finally:
        await service.close()
        lock.release()


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


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE pairs from a single file."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8")
    except PermissionError:
        logging.getLogger(__name__).warning(
            "Cannot read %s (permission denied) — skipping", path,
        )
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _load_env_file() -> dict[str, str]:
    """Load KEY=VALUE pairs, merging system + user files on Linux.

    User-local values take precedence over the system file so that
    non-root ``configure --set`` always wins.

    Files only -- this is what gets written back out. To *read* the agent's
    configuration, use :func:`_effective_env`.
    """
    if _IS_WINDOWS:
        return _parse_env_file(_WIN_ENV_FILE)
    # System-wide first, then overlay user-local
    merged = _parse_env_file(_LINUX_SYSTEM_ENV_FILE)
    merged.update(_parse_env_file(_LINUX_USER_ENV_FILE))
    return merged


def _effective_env() -> dict[str, str]:
    """The configuration as the agent will actually see it.

    Files, then the process environment on top.  ``run`` has always read the
    environment -- that is what ``AgentConfig.from_env`` does -- while the
    commands around it read only the files. An agent configured entirely
    through ``LLM_PORT_NODE_AGENT_*`` variables, which is how a container or
    a unit file does it, therefore joined under its container hostname and
    advertised the wrong address, and ``join`` refused to run at all for want
    of a backend URL it had been handed.
    """
    merged = _load_env_file()
    merged.update({
        key: value
        for key, value in os.environ.items()
        if key.startswith(_ENV_PREFIX) and value.strip()
    })
    return merged


def _save_env_file(env: dict[str, str]) -> Path:
    """Write env dict to the platform env file."""
    path = _env_file_path()
    lines = [f"{k}={v}" for k, v in sorted(env.items())]
    content = "\n".join(lines) + "\n"
    if _IS_WINDOWS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    # Linux — try direct write first
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(str(path), 0o600)
        return path
    except PermissionError:
        pass

    # Try sudo for system-wide path
    if path == _LINUX_SYSTEM_ENV_FILE:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
            f.write(content)
            tmp = f.name
        try:
            sudo = _sudo_prefix()
            rc = _run_cmd([*sudo, "install", "-m", "0600", tmp, str(path)])
            if rc == 0:
                return path
        finally:
            Path(tmp).unlink(missing_ok=True)

        # sudo failed — fall back to user-local file
        _warn(f"Cannot write to {path} (no sudo). Saving to user config instead.")
        path = _LINUX_USER_ENV_FILE

    # Write to user-local path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(str(path), 0o600)
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
    file_env = _effective_env()
    live_env = {k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIX)}
    merged = {**file_env, **live_env}

    _section("Current Configuration")

    if _IS_WINDOWS:
        config_paths = [_WIN_ENV_FILE]
    else:
        config_paths = [_LINUX_SYSTEM_ENV_FILE, _LINUX_USER_ENV_FILE]

    found_any = False
    for cp in config_paths:
        if cp.exists():
            _ok(f"Config file: {cp}")
            found_any = True
    if not found_any:
        _warn(f"No config file found (checked {', '.join(str(p) for p in config_paths)})")

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


# ── HF cache detection ──────────────────────────────────────────


def _count_hf_models(path: Path) -> int:
    """Count ``models--*`` directories in *path*."""
    if not path.is_dir():
        return 0
    return sum(1 for d in path.iterdir() if d.is_dir() and d.name.startswith("models--"))


def _detect_hf_caches() -> list[tuple[Path, int]]:
    """Scan well-known locations for existing HuggingFace caches.

    Returns a list of ``(path, model_count)`` tuples, sorted by model
    count descending.  Only paths with at least one ``models--*``
    directory are returned.
    """
    candidates: list[Path] = []

    # 1. Environment variables (highest priority)
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        val = os.environ.get(var)
        if val:
            candidates.append(Path(val))

    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        candidates.append(Path(hf_home) / "hub")

    # 2. Platform defaults
    if _IS_WINDOWS:
        candidates.append(Path.home() / ".cache" / "huggingface" / "hub")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "huggingface" / "hub")
    else:
        candidates.append(Path.home() / ".cache" / "huggingface" / "hub")
        # Common server-side paths
        candidates.append(Path("/srv/llm-port/models"))
        candidates.append(Path("/data/hf-cache"))

    # 3. Current model store (if already configured)
    existing = _load_env_file()
    cur_store = existing.get(f"{_ENV_PREFIX}MODEL_STORE")
    if cur_store:
        candidates.append(Path(cur_store))

    # Deduplicate by resolved path, count models
    seen: set[Path] = set()
    results: list[tuple[Path, int]] = []
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        count = _count_hf_models(resolved)
        if count > 0:
            results.append((c, count))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


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

    # Auto-detect existing HuggingFace caches
    detected_caches = _detect_hf_caches()
    default_store = "/srv/llm-port/models" if not _IS_WINDOWS else r"C:\llm-port\models"
    current_store = _cur("MODEL_STORE")

    if detected_caches:
        print(f"  {_GREEN}Found HuggingFace cache(s):{_RESET}")
        for idx, (path, count) in enumerate(detected_caches, 1):
            print(f"    [{idx}] {path} ({count} model{'s' if count != 1 else ''})")
        print(f"    [{len(detected_caches) + 1}] Enter a custom path")
        print()

        try:
            choice = input(f"  {_BOLD}Select [1-{len(detected_caches) + 1}]:{_RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

        try:
            idx = int(choice)
            if 1 <= idx <= len(detected_caches):
                model_store = str(detected_caches[idx - 1][0])
            else:
                model_store = _prompt("Model store path", default=current_store or default_store)
        except ValueError:
            model_store = _prompt("Model store path", default=current_store or default_store)
    else:
        model_store = _prompt("Model store path", default=current_store or default_store)

    env[f"{_ENV_PREFIX}MODEL_STORE"] = model_store

    _section("Logging & Monitoring")

    # Determine the backend host for smart Loki default
    # Blank is the right answer almost always: the agent then asks LLM.Port
    # where to send its logs, which keeps working if Loki moves.
    loki_url = _prompt(
        "Loki URL (blank: LLM.Port says where to send logs)",
        default=_cur("LOKI_URL") or "",
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
        print(f"    llmport-agent init    One-time host setup (sudoers)")
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
    print("  [2] Init        — one-time host setup (sudoers, directories)")
    print("  [3] Run         — run agent in the foreground")
    print("  [4] Start       — install and start as a system service")
    print("  [5] Exit")
    print()

    try:
        choice = input(f"  {_BOLD}Select [1-5]:{_RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if choice == "1":
        cmd_configure()
    elif choice == "2":
        cmd_init()
    elif choice == "3":
        _load_env_into_process()
        asyncio.run(_run())
    elif choice == "4":
        _load_env_into_process()
        cmd_start()
    else:
        return


def _load_env_into_process() -> None:
    """Load saved env file into the current process environment."""
    file_env = _effective_env()
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


def _resolve_service_user() -> tuple[str, str, str]:
    """Return ``(username, group, home_dir)`` for the service.

    When the invoking user is root and a ``llm-port-agent`` system user
    exists, use that dedicated user.  Otherwise run as the invoking user
    (or ``SUDO_USER`` when invoked via sudo).
    """
    import grp  # noqa: PLC0415
    import pwd  # noqa: PLC0415

    # If invoked via sudo, prefer the real user behind sudo
    real_user = os.environ.get("SUDO_USER", "")
    if real_user:
        try:
            pw = pwd.getpwnam(real_user)
            gr = grp.getgrgid(pw.pw_gid)
            return pw.pw_name, gr.gr_name, pw.pw_dir
        except KeyError:
            pass

    uid = os.getuid()  # type: ignore[attr-defined]
    pw = pwd.getpwuid(uid)
    gr = grp.getgrgid(pw.pw_gid)
    return pw.pw_name, gr.gr_name, pw.pw_dir


def _build_service_content(agent_bin: str) -> str:
    svc_user, svc_group, home_dir = _resolve_service_user()
    user_env_file = Path(home_dir) / ".config" / SERVICE_NAME / "agent.env"

    # Determine model_store from current env (may differ from default)
    merged = _effective_env()
    model_store = merged.get(f"{_ENV_PREFIX}MODEL_STORE", "/srv/llm-port/models")
    state_dir = merged.get(
        f"{_ENV_PREFIX}STATE_PATH",
        f"/var/lib/llmport-agent" if svc_user == "root" else f"{home_dir}/.local/share/llmport-agent",
    )
    # STATE_PATH points to the file; we need its parent directory
    if state_dir.endswith(".json"):
        state_dir = str(Path(state_dir).parent)

    # Collect writable paths — deduplicate
    rw_paths_set: set[str] = {state_dir, model_store}
    # If model store or state dir is under the user's home, we need home access
    is_home_model = model_store.startswith(home_dir)
    is_home_state = state_dir.startswith(home_dir)
    needs_home = is_home_model or is_home_state
    if is_home_model:
        rw_paths_set.add(model_store)
    if is_home_state:
        rw_paths_set.add(state_dir)
    # The user-local config dir should also be writable
    user_config_dir = str(Path(home_dir) / ".config" / SERVICE_NAME)
    if needs_home:
        rw_paths_set.add(user_config_dir)
    rw_paths = " ".join(sorted(rw_paths_set))

    replacements = {
        "@@USER@@": svc_user,
        "@@GROUP@@": svc_group,
        "@@USER_ENV_FILE@@": str(user_env_file),
    }

    template = _find_service_template()
    if template:
        content = template.read_text(encoding="utf-8")
        content = re.sub(
            r"^ExecStart=.*$",
            f"ExecStart={agent_bin} run",
            content,
            flags=re.MULTILINE,
        )
    else:
        content = (
            "[Unit]\n"
            "Description=llm-port node agent\n"
            "After=network-online.target docker.service\n"
            "Wants=network-online.target\n"
            # A start that can never succeed must stop retrying and say so.
            # The agent handles a backend outage itself -- it reconnects with backoff
            # and never exits for that -- so a failed *start* means something
            # structural, most often a second agent already holding the state lock.
            # Without a limit systemd retries every 5s forever while reporting
            # "activating (auto-restart)", which reads like a slow boot rather than
            # a wedged service: observed on the DGX head at restart counter 9259,
            # roughly fifteen hours of looping that nothing surfaced.
            "StartLimitIntervalSec=300\n"
            "StartLimitBurst=10\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"User=@@USER@@\n"
            f"Group=@@GROUP@@\n"
            "SupplementaryGroups=docker\n"
            f"EnvironmentFile=-/etc/{SERVICE_NAME}.env\n"
            f"EnvironmentFile=-@@USER_ENV_FILE@@\n"
            f"ExecStart={agent_bin} run\n"
            "Restart=always\n"
            "RestartSec=5\n\n"
            "NoNewPrivileges=false\n"
            "PrivateTmp=true\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    return content


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


_SUDOERS_FILE = Path("/etc/sudoers.d/llmport-agent")
_SUDOERS_CMDS = ["apt", "fwupdmgr"]


def _emit(text: str) -> None:
    """Print text that may not survive the terminal's encoding.

    A node booted with LANG=C has an ASCII stdout, and the company name in
    NOTICE is not ASCII -- so a plain print() there raises
    UnicodeEncodeError and the licence command tracebacks on exactly the
    machines it exists for. Degrade the characters, never the command.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        sys.stdout.write(text.encode(encoding, "replace").decode(encoding))
        print()


def cmd_license() -> None:
    """Print the licence and attribution notice this build carries.

    The agent is Apache-2.0 and ships a NOTICE, and section 4(d) requires
    that notice to accompany the distribution. The binary bundles both
    files; this is how somebody holding only the binary reads them.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    shown = False
    for name in ("NOTICE", "LICENSE"):
        path = base / name
        if not path.is_file():
            continue
        if shown:
            _emit("")
        _emit(path.read_text(encoding="utf-8").rstrip())
        shown = True

    if shown:
        return

    # Never imply a licence whose text we cannot actually produce.
    _emit(f"llmport-agent {_AGENT_VERSION}")
    _emit("Copyright 2026 Emagin8 UG (haftungsbeschraenkt) and contributors.")
    _emit("Licensed under the Apache License, Version 2.0.")
    _emit("http://www.apache.org/licenses/LICENSE-2.0")
    _emit("")
    _emit("(The full text was not bundled with this build.)")


def cmd_init() -> None:
    """One-time host initialisation: sudoers, directories, etc."""
    if _IS_WINDOWS:
        print("init is not required on Windows.")
        return

    _require_linux()
    sudo = _sudo_prefix()
    svc_user, _, _ = _resolve_service_user()

    # ── sudoers for passwordless apt / fwupdmgr ──
    # Resolve absolute paths (required by sudoers for security).
    resolved = [shutil.which(c) for c in _SUDOERS_CMDS]
    cmds = ", ".join(p for p in resolved if p)
    if not cmds:
        print("  No privileged commands found on this system — skipping sudoers.")
    elif _SUDOERS_FILE.exists():
        print(f"  {_SUDOERS_FILE} already exists — skipping.")
    else:
        sudoers_line = f"{svc_user} ALL=(ALL) NOPASSWD: {cmds}\n"
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".sudoers") as sf:
            sf.write(sudoers_line)
            tmp = sf.name
        try:
            if _run_cmd([*sudo, "install", "-m", "0440", tmp, str(_SUDOERS_FILE)]) == 0:
                print(f"  Installed {_SUDOERS_FILE}")
                print(f"    {svc_user} NOPASSWD: {cmds}")
            else:
                print(f"  ERROR: Failed to install {_SUDOERS_FILE}", file=sys.stderr)
        finally:
            os.unlink(tmp)

    print("\n  Host initialisation complete.")


async def _existing_membership(config: AgentConfig) -> dict | None:
    """The fleet membership this machine already has, if its credential still works."""
    from llm_port_node_agent.backend_client import BackendClient  # noqa: PLC0415
    from llm_port_node_agent.state_store import StateStore  # noqa: PLC0415

    try:
        credential = StateStore(config.state_path).state.credential
    except Exception:  # noqa: BLE001 - no usable state is simply "not a member"
        return None
    if not credential:
        return None
    client = BackendClient(config)
    try:
        return await client.whoami(credential=credential)
    except Exception:  # noqa: BLE001 - unreachable: let the join report it
        return None
    finally:
        await client.close()


async def _join_flow(config: AgentConfig) -> bool:
    """Ask the backend to let this machine in, then wait for a human.

    The whole point of this path is that nothing long is typed here.  The
    operator types a backend address; the code we print is short enough to
    read across a room, and it is a *confirmation* value rather than a secret
    -- the credential only ever comes back to this process, which is the one
    holding the poll secret.

    Returns:
        True once a credential has been stored, False if it was refused or
        timed out.
    """
    from llm_port_node_agent.backend_client import BackendClient
    from llm_port_node_agent.gpu import detect_gpu
    from llm_port_node_agent.preflight import build_static_capabilities
    from llm_port_node_agent.runtimes import detect_runtime
    from llm_port_node_agent.state_store import StateStore

    # The same detection the service does, and for the same reason: what a
    # node reports at join is what decides which runtime image it can run, so
    # a join that describes the machine differently from the service would
    # enrol a node the cluster then refuses.
    #
    # ``GpuCollector`` is the Protocol, not a collector -- constructing it
    # raised "Protocols cannot be instantiated" and took the whole join with
    # it.
    runtime = detect_runtime(preferred=config.container_runtime)
    capabilities = await build_static_capabilities(
        runtime,
        detect_gpu(),
        paths={
            "model_store": config.model_store_root,
            "ray_session": config.ray_session_dir,
        },
    )

    client = BackendClient(config)
    try:
        asked = await client.request_join(
            agent_id=config.agent_id,
            host=config.advertise_host,
            capabilities=capabilities,
            version=_AGENT_VERSION,
        )
    except Exception as exc:  # noqa: BLE001 - the message is the product here
        _err(f"Could not reach {config.backend_url}: {exc}")
        return False

    code = asked.get("code", "?")
    poll_secret = asked.get("poll_secret")
    request_id = asked.get("id")

    _section("Waiting for approval")
    print()
    print(f"      Code:  {code}")
    print()
    print("  Open LLM.Port in a browser, go to Machines, and approve this")
    print("  request.  Check the code above matches the one on screen.")
    print()

    if not poll_secret:
        # This machine already had a live request, so the secret belongs to
        # the process that made it.  Saying so beats polling forever.
        _warn("This machine is already waiting for approval from an earlier run.")
        _warn("Approve it in the browser; that run will pick up the credential.")
        return False

    # A human has to walk to a browser, so poll patiently rather than tightly.
    deadline = time.monotonic() + _JOIN_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(_JOIN_POLL_SECONDS)
        try:
            result = await client.collect_join(request_id=request_id, poll_secret=poll_secret)
        except Exception as exc:  # noqa: BLE001
            _warn(f"Could not reach the backend: {exc}")
            continue

        status = result.get("status")
        if status == "pending":
            continue
        if status == "approved":
            store = StateStore(config.state_path)
            store.load()
            store.state.credential = result.get("credential")
            store.state.node_id = result.get("node_id")
            store.save()
            _ok(f"Approved.  This machine is now '{result.get('agent_id')}'.")
            return True
        if status == "rejected":
            _err(f"The request was refused. {result.get('message') or ''}".strip())
            return False
        _err(f"The request is no longer usable ({status}).")
        return False

    _err("Nobody approved this within the time limit.  Run join again to retry.")
    return False


def cmd_join(backend_url: str | None, user: bool | None = None) -> None:
    """Enrol this machine by asking, rather than by carrying a token to it.

    ``user`` is passed on to ``start``: a user service needs no root.
    """
    _banner()

    merged = _effective_env()
    backend = (backend_url or merged.get(f"{_ENV_PREFIX}BACKEND_URL") or "").strip()
    if not backend:
        _err("Where is LLM.Port?  Usage: llmport-agent join http://<backend-host>:8000")
        sys.exit(1)
    if "://" not in backend:
        # "10.88.10.220:8000" is what a person types; accept it.
        backend = f"http://{backend}"

    hostname = socket.gethostname()
    agent_id = merged.get(f"{_ENV_PREFIX}AGENT_ID") or hostname
    host = merged.get(f"{_ENV_PREFIX}ADVERTISE_HOST") or merged.get(f"{_ENV_PREFIX}HOST") or hostname

    _section("This machine")
    _kv("Name", agent_id)
    _kv("Address", host)
    _kv("LLM.Port", backend)

    # Build the config the same way the service will, so a join that works
    # is a guarantee the service will reach the same backend the same way.
    #
    # The *whole* configuration, not the three keys this used to promote. A
    # join reports the machine -- and since that now includes the host paths
    # the runtime container is mounted through, a join that read only the
    # backend, the name and the address described a node with default paths
    # and the cluster mounted the wrong directories.
    _load_env_into_process()
    os.environ[f"{_ENV_PREFIX}BACKEND_URL"] = backend
    os.environ[f"{_ENV_PREFIX}AGENT_ID"] = agent_id
    os.environ[f"{_ENV_PREFIX}HOST"] = host
    config = AgentConfig.from_env()

    # Already a member? Then this is an upgrade, not a join. Running the same
    # install line again is how an operator updates the agent, and it used to
    # file a fresh join request for a machine already in the fleet -- one
    # more thing to approve, for nothing.
    member = asyncio.run(_existing_membership(config))
    if member is not None:
        _section("Already a member")
        _ok(f"This machine is '{member.get('agent_id')}' on {backend}; nothing to approve.")
        _section("Starting the agent")
        cmd_start(user=user)
        return

    if not asyncio.run(_join_flow(config)):
        sys.exit(1)

    # Persist what the service will need, then hand over to `start`.
    env = _load_env_file()
    env[f"{_ENV_PREFIX}BACKEND_URL"] = backend
    env[f"{_ENV_PREFIX}AGENT_ID"] = agent_id
    env[f"{_ENV_PREFIX}HOST"] = host
    # An enrollment token left over from a previous attempt is now misleading.
    env.pop(f"{_ENV_PREFIX}ENROLLMENT_TOKEN", None)
    _save_env_file(env)

    _section("Starting the agent")
    cmd_start(user=user)


def cmd_start(user: bool | None = None) -> None:
    """Install and start llmport-agent as a background service.

    ``user`` picks a ``systemd --user`` unit (no privilege needed) over the
    system unit; ``None`` decides from what this machine allows.
    """
    _load_env_into_process()
    env_lines = _collect_env_lines()

    if not any(line.startswith(f"{_ENV_PREFIX}BACKEND_URL=") for line in env_lines):
        _err("BACKEND_URL is not set. Run 'llmport-agent configure' first.")
        sys.exit(1)

    agent_bin = _agent_binary()
    if not agent_bin:
        print(
            "ERROR: cannot work out where llmport-agent is installed.",
            file=sys.stderr,
        )
        sys.exit(1)

    if _IS_WINDOWS:
        _cmd_start_windows(agent_bin, env_lines)
    elif _choose_user_scope(user):
        _cmd_start_linux_user(agent_bin, env_lines)
    else:
        _cmd_start_linux(agent_bin, env_lines)


# -- the service without root --------------------------------------------------

_USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


def _user_unit_path() -> Path:
    return _USER_UNIT_DIR / f"{SERVICE_NAME}.service"


def _system_unit_path() -> Path:
    return Path(f"/etc/systemd/system/{SERVICE_NAME}.service")


def _user_services_available() -> bool:
    """Whether this user has a running systemd user manager."""
    try:
        probe = subprocess.run(  # noqa: S603
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.stdout.strip() in {"running", "degraded", "starting"}


def _passwordless_sudo() -> bool:
    if os.getuid() == 0:  # type: ignore[attr-defined]
        return True
    try:
        return subprocess.run(  # noqa: S603
            ["sudo", "-n", "true"], capture_output=True, timeout=10
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _choose_user_scope(requested: bool | None) -> bool:
    """User unit or system unit.

    Asked for explicitly, that wins. Otherwise a system unit whenever root is
    to hand -- running as root, or sudo without a password -- and a user unit
    only when nobody is there to type a password and the user manager runs.
    That last case is the one that used to fail outright: a non-interactive
    install on a machine whose sudo needs a password installed the binary,
    then died writing the unit, leaving an agent that stopped at logout.
    """
    if requested is not None:
        return requested
    if _IS_WINDOWS or _passwordless_sudo():
        return False
    return not sys.stdin.isatty() and _user_services_available()


def _build_user_service_content(agent_bin: str, env_file: Path) -> str:
    return (
        "[Unit]\n"
        "Description=LLM.Port node agent (user service)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"EnvironmentFile={env_file}\n"
        f"ExecStart={agent_bin} run\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _cmd_start_linux_user(agent_bin: str, env_lines: list[str]) -> None:
    """Run the agent as a ``systemd --user`` service: no sudo anywhere.

    Linger is what makes this a real service rather than a login session's
    child: without it the user manager, and the agent with it, stops when
    the last session ends. Enabling it for oneself needs no privilege on
    DGX OS; where it does, the agent still runs and the operator is told
    exactly what to ask an administrator for.
    """
    _require_linux()
    env_file = _LINUX_USER_ENV_FILE
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    env_file.chmod(0o600)

    _USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    _user_unit_path().write_text(
        _build_user_service_content(agent_bin, env_file), encoding="utf-8"
    )

    if _run_cmd(["systemctl", "--user", "daemon-reload"]) != 0:
        sys.exit(1)
    if _run_cmd(["systemctl", "--user", "enable", SERVICE_NAME]) != 0:
        sys.exit(1)
    # restart, not "enable --now": that only starts a stopped service, so an
    # upgrade replaced the binary, reported success, and left the old build
    # running from the replaced file until the machine next rebooted.
    if _run_cmd(["systemctl", "--user", "restart", SERVICE_NAME]) != 0:
        sys.exit(1)

    user = getpass.getuser()
    lingering = _run_cmd(["loginctl", "enable-linger", user], check=False, quiet=True) == 0

    print(f"\n  {SERVICE_NAME} installed and started as a user service -- no root needed.")
    print(f"  Running as: {user}")
    print(f"  View logs:  journalctl --user -u {SERVICE_NAME} -f")
    print("  Stop:       llmport-agent stop")
    if not lingering:
        print(
            f"\n  NOTE: could not enable linger for {user}, so the agent stops when\n"
            f"  you log out. An administrator can fix that once with:\n"
            f"      sudo loginctl enable-linger {user}"
        )


def _agent_binary() -> str | None:
    """Where this agent is installed, for the service unit to point at.

    ``shutil.which`` alone finds it only when its directory is on PATH, which
    a virtualenv install is not unless the venv is activated -- and a service
    unit does not activate anything. But the process running this code *is*
    the agent, so its own path is the answer whenever ``which`` has none.
    """
    found = shutil.which("llmport-agent")
    if found:
        return found
    launched = Path(sys.argv[0]).resolve()
    if launched.is_file() and os.access(launched, os.X_OK):
        return str(launched)
    # Installed as a module rather than through its console script.
    sibling = Path(sys.executable).resolve().parent / "llmport-agent"
    return str(sibling) if sibling.is_file() else None


def _cmd_start_linux(agent_bin: str, env_lines: list[str]) -> None:
    _require_linux()
    sudo = _sudo_prefix()

    svc_user, svc_group, _ = _resolve_service_user()
    service_content = _build_service_content(agent_bin)
    env_content = "\n".join(env_lines) + "\n"

    # Resolve model_store to ensure state/model dirs exist with correct ownership
    merged = _effective_env()
    model_store = merged.get(f"{_ENV_PREFIX}MODEL_STORE", "/srv/llm-port/models")
    state_dir = "/var/lib/llmport-agent"

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
        if _run_cmd([*sudo, "install", "-m", "0600", tmp_env, str(_LINUX_SYSTEM_ENV_FILE)]) != 0:
            sys.exit(1)

        # Ensure state and model dirs exist with correct ownership
        _run_cmd([*sudo, "mkdir", "-p", state_dir], check=False, quiet=True)
        _run_cmd([*sudo, "chown", f"{svc_user}:{svc_group}", state_dir], check=False, quiet=True)
        _run_cmd([*sudo, "mkdir", "-p", model_store], check=False, quiet=True)
        _run_cmd([*sudo, "chown", f"{svc_user}:{svc_group}", model_store], check=False, quiet=True)

        # Ensure the service user is in the docker group
        _run_cmd([*sudo, "usermod", "-aG", "docker", svc_user], check=False, quiet=True)

        if _run_cmd([*sudo, "systemctl", "daemon-reload"]) != 0:
            sys.exit(1)
        if _run_cmd([*sudo, "systemctl", "enable", SERVICE_NAME]) != 0:
            sys.exit(1)
        # restart, so an upgrade runs the build it just installed; "enable
        # --now" leaves an already-running service on the old one.
        if _run_cmd([*sudo, "systemctl", "restart", SERVICE_NAME]) != 0:
            sys.exit(1)
    finally:
        if tmp_svc:
            os.unlink(tmp_svc)
        if tmp_env:
            os.unlink(tmp_env)

    print(f"\n  {SERVICE_NAME} service installed and started.")
    print(f"  Running as: {svc_user}:{svc_group}")
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


def _own_process_chain() -> set[int]:
    """This process and every ancestor of it.

    An ancestor's command line can contain the very pattern we match on --
    ``sh -c "llmport-agent run"``, or the terminal launched from one -- and
    killing an ancestor takes this process down with it.  Learned the hard
    way once already: a workspace reclaim matched a parent process and closed
    the terminal that had invoked it.
    """
    chain = {os.getpid()}
    try:
        for parent in psutil.Process().parents():
            chain.add(parent.pid)
    except psutil.Error:  # pragma: no cover - platform dependent
        pass
    return chain


def _running_agents() -> list[psutil.Process]:
    """Agent processes currently running on this host, excluding our own.

    Matched on the command line rather than a pidfile because the
    single-instance lock deliberately stores no pid -- the kernel releasing
    the lock is the whole mechanism, and writing into the locked byte would
    defeat it.  ``run`` is required so that a concurrent ``llmport-agent
    stop`` never matches itself.
    """
    mine = _own_process_chain()
    found: list[psutil.Process] = []
    for proc in psutil.process_iter(["cmdline"]):
        if proc.pid in mine:
            continue
        try:
            cmdline = proc.info.get("cmdline") or []
        except psutil.Error:  # pragma: no cover - races with process exit
            continue
        if _is_agent_cmdline(cmdline):
            found.append(proc)
    return found


def _is_agent_cmdline(cmdline: list[str]) -> bool:
    """Whether *cmdline* is an agent running in the foreground.

    The agent token has to be immediately followed by ``run``.  Testing the
    two separately is far too loose on Windows, where psutil re-splits the
    raw command line rather than reporting real argv: a shell invoked with a
    long ``-c`` string comes back as dozens of tokens, and any such string
    that mentions the agent path and the word "run" anywhere in it matched.
    A live check caught exactly that -- the scan found an unrelated shell
    alongside the process it was meant to find.  Adjacency is the thing that
    actually distinguishes running the agent from talking about it.
    """
    if not cmdline or Path(cmdline[0]).name.lower() in _SHELL_NAMES:
        return False
    for index, token in enumerate(cmdline[:-1]):
        if Path(token).name in _AGENT_EXE_NAMES and cmdline[index + 1] == "run":
            return True
    return False


def _stop_stray_agents() -> int:
    """Stop agents no service manager knows about.  Returns how many.

    ``systemctl disable --now`` stops the unit and nothing else, so an agent
    started by hand -- ``llmport-agent run``, which is exactly what the
    source install leaves you with -- outlives the uninstall.  It keeps its
    backend session open and the node keeps reporting healthy long after the
    binary, the unit and the config file have all been deleted, because it
    read its configuration at startup and never looks again.

    Seen on a live node: the agent had been "removed", and was still
    streaming to the backend twelve hours later.
    """
    strays = _running_agents()
    if not strays:
        return 0

    for proc in strays:
        print(f"  Stopping agent process {proc.pid} (not managed by a service).")
        _signal_agent(proc, "terminate")

    _gone, alive = psutil.wait_procs(strays, timeout=10)
    for proc in alive:
        _signal_agent(proc, "kill")
    if alive:
        psutil.wait_procs(alive, timeout=5)

    remaining = {proc.pid for proc in _running_agents()}
    if remaining:
        print(
            f"  WARNING: could not stop agent process(es) {sorted(remaining)} -- "
            "stop them by hand, or the node will keep reporting healthy.",
            file=sys.stderr,
        )
    return len(strays) - len(remaining)


def _signal_agent(proc: psutil.Process, how: str) -> None:
    """Terminate or kill *proc*, escalating to sudo when it is not ours."""
    try:
        getattr(proc, how)()
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied:
        # Running as a service account. Ask for the privilege rather than
        # reporting a stop that did not happen.
        if _IS_WINDOWS:
            _run_cmd(["taskkill", "/F", "/PID", str(proc.pid)], check=False, quiet=True)
        else:
            signal = "-KILL" if how == "kill" else "-TERM"
            _run_cmd([*_sudo_prefix(), "kill", signal, str(proc.pid)], check=False, quiet=True)


def cmd_stop() -> None:
    """Stop and remove the llmport-agent background service."""
    if _IS_WINDOWS:
        _cmd_stop_windows()
    else:
        _cmd_stop_linux()


def _cmd_stop_linux() -> None:
    _require_linux()
    if _user_unit_path().exists():
        _run_cmd(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
        _user_unit_path().unlink(missing_ok=True)
        _run_cmd(["systemctl", "--user", "daemon-reload"], check=False, quiet=True)
        print(f"{SERVICE_NAME} user service stopped and removed.")
    # A system unit needs root to remove -- and only then is sudo worth
    # asking for. It used to be asked for unconditionally, so stopping an
    # agent that had never been a system service still wanted a password.
    if _system_unit_path().exists():
        sudo = _sudo_prefix()
        _run_cmd([*sudo, "systemctl", "disable", "--now", SERVICE_NAME], check=False)
        print(f"{SERVICE_NAME} service stopped and disabled.")
    _stop_stray_agents()


def _cmd_stop_windows() -> None:
    pid = _win_read_pid()
    if pid is not None:
        _run_cmd(["taskkill", "/F", "/PID", str(pid)], check=False, quiet=True)
    # Also try by name in case PID file is stale
    _run_cmd(["taskkill", "/F", "/IM", "llmport-agent.exe"], check=False, quiet=True)
    # taskkill matches the image name, so it never sees a source install --
    # that runs as python.exe with the agent as an argument.
    _stop_stray_agents()
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
    if _user_unit_path().exists():
        os.execlp("systemctl", "systemctl", "--user", "status", SERVICE_NAME)
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


def cmd_scan() -> None:
    """Scan and display models in the configured model store."""
    _banner()
    merged = _effective_env()
    store = merged.get(f"{_ENV_PREFIX}MODEL_STORE", "/srv/llm-port/models")
    store_path = Path(store)

    _section(f"Model Cache: {store}")

    if not store_path.is_dir():
        _warn(f"Directory does not exist: {store}")
        return

    # Try huggingface_hub.scan_cache_dir for rich output
    try:
        from huggingface_hub import scan_cache_dir  # noqa: PLC0415

        info = scan_cache_dir(store_path)
        if not info.repos:
            _warn("No models found in cache.")
            return

        models = [r for r in info.repos if r.repo_type == "model"]
        for repo in sorted(models, key=lambda r: r.repo_id):
            refs = ", ".join(sorted(repo.refs)) or "(detached)"
            _kv(repo.repo_id, f"{repo.size_on_disk_str}  {repo.nb_files} files  refs: {refs}")

        print()
        _ok(f"{len(models)} model(s), {info.size_on_disk_str} total")
        if info.warnings:
            _warn(f"{len(info.warnings)} corrupted cache entries skipped")
        return
    except ImportError:
        pass  # fall through to basic scan
    except Exception as exc:
        _warn(f"scan_cache_dir failed: {exc} — falling back to basic scan")

    # Basic fallback: just list models--* directories
    model_dirs = sorted(d for d in store_path.iterdir() if d.is_dir() and d.name.startswith("models--"))
    if not model_dirs:
        _warn("No models found in cache.")
        return

    for d in model_dirs:
        repo_id = d.name.split("--", 1)[1].replace("--", "/") if "--" in d.name else d.name
        blob_count = sum(1 for _ in (d / "blobs").iterdir()) if (d / "blobs").is_dir() else 0
        _kv(repo_id, f"{blob_count} blobs")

    print()
    _ok(f"{len(model_dirs)} model(s)")


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
    sub.add_parser("scan", help="Scan and list models in the model cache")
    sub.add_parser("init", help="One-time host setup (sudoers, directories)")
    p_join = sub.add_parser(
        "join",
        help="Ask to join a cluster and wait for an administrator to approve",
    )
    p_join.add_argument(
        "backend",
        nargs="?",
        metavar="BACKEND_URL",
        help="Where LLM.Port is, e.g. http://10.88.10.220:8000",
    )
    p_join.add_argument(
        "--user",
        action="store_true",
        default=None,
        help="Run as a systemd user service afterwards: no sudo needed",
    )
    sub.add_parser("run", help="Run agent in the foreground")
    p_start = sub.add_parser("start", help="Install and start as a background service")
    p_start.add_argument(
        "--user",
        action="store_true",
        default=None,
        help="Install a systemd user service instead of a system one: no sudo needed",
    )
    sub.add_parser("stop", help="Stop and remove the background service")
    sub.add_parser("status", help="Show background service status")
    sub.add_parser("license", help="Show licence and attribution for this build")

    args = parser.parse_args()

    if args.command is None:
        cmd_interactive()
    elif args.command == "show":
        cmd_show()
    elif args.command == "scan":
        cmd_scan()
    elif args.command == "join":
        cmd_join(args.backend, user=args.user)
    elif args.command == "init":
        cmd_init()
    elif args.command == "configure":
        if args.set_pairs:
            cmd_configure_set(args.set_pairs)
        else:
            cmd_configure()
    elif args.command == "run":
        cmd_run()
    elif args.command == "start":
        cmd_start(user=args.user)
    elif args.command == "stop":
        cmd_stop()
    elif args.command == "status":
        cmd_status()
    elif args.command == "license":
        cmd_license()


if __name__ == "__main__":
    main()
