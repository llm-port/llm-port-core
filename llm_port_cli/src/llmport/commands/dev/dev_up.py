"""``llmport dev up`` — start the development servers.

Mirrors the logic of ``start-dev.ps1`` / ``start-dev.sh``:
  1. Start shared infrastructure (if not already running)
  2. Install/sync dependencies
  3. Run migrations
  4. Launch backend, taskiq worker, and frontend
"""

from __future__ import annotations

import os
import platform
import signal
import socket
import time
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import click
import psutil

from llmport.core.console import console, success, warning, error, info
from llmport.core.registry import DEV_ENDPOINTS, DEV_MODULE_ENDPOINTS, ModuleInfo
from llmport.core.settings import load_config
from llmport.core.workspace import (
    find_service_dir,
    resolve_shared_compose,
    resolve_workspace,
)

from .dev_group import dev_group


def _find_workspace() -> Path:
    """The workspace to start, remembering one we had to detect.

    ``dev init`` used to be a precondition purely because it was the only
    thing that wrote ``dev.workspace_dir``.  Detection removes that: a
    developer who cloned the repos themselves can run ``dev up`` directly,
    from the workspace root or from inside any service.
    """
    return resolve_workspace(remember=True)


def _ensure_databases(backend_dir: Path) -> None:
    """Create the per-service databases if they are absent.

    Only ``dev init`` did this before, which is what made it a hard
    precondition: without the databases the Alembic step below fails on a
    checkout that was never initialised, and equally after
    ``dev down --volumes`` wipes them.  Both are recoverable, so recover.
    """
    from llmport.commands.dev.dev_init import _ensure_backend_role, _ensure_database
    from llmport.core.registry import DATABASES

    created = [name for name in DATABASES if _ensure_database(name, quiet=True)]
    if created:
        success(f"Created missing databases: {', '.join(created)}")
    if backend_dir.exists():
        _ensure_backend_role(backend_dir)


#: The Windows Terminal window these tabs go into.
#:
#: Named rather than numbered so a second ``dev up`` reuses the same window
#: instead of opening another, and so the tabs never land in a window the
#: operator is working in.
_WT_WINDOW_NAME = "llmport-dev"


def _launch_terminal(title: str, working_dir: Path, command: str, headless: bool) -> bool:
    """Start a dev process.

    With a GUI (terminal emulator on PATH / a remote SSH session that has
    a DISPLAY), spawn a terminal window.  Otherwise fall back to a
    background process (headless).  Returns False if the process could
    not be started at all.
    """
    system = platform.system()

    if headless or not _can_open_terminal(system):
        return _launch_background(working_dir, command, label=title.split("–")[0].strip().lower())

    if system == "Windows":
        # Resolve shell: pwsh (PS7) > powershell (PS5) > cmd
        shell = _which("pwsh") or _which("powershell") or "cmd"
        shell_name = os.path.basename(shell).lower()

        wt = _which("wt")
        if wt:
            # ``-w`` is what makes these tabs rather than windows.
            #
            # ``wt new-tab`` on its own opens a *new window* every time it is
            # invoked -- "new-tab" describes what it puts in the window, not
            # where it puts it -- so starting four services gave four windows
            # scattered across the desktop.
            #
            # Naming the window fixes that, and Terminal creates it on first
            # use: "If no window exists with the given window-id, then a new
            # window will be created with that id/name." So the backend opens
            # the window and the worker, gateway and frontend land beside it.
            #
            # A name rather than ``0`` ("most recent window") on purpose: with
            # ``0`` the tabs would land in whichever Terminal window the
            # operator happened to touch last, which may be one they are
            # working in.
            subprocess.Popen(
                [
                    wt, "-w", _WT_WINDOW_NAME, "new-tab",
                    "--title", title,
                    "--startingDirectory", str(working_dir),
                    shell, "-NoExit", "-Command", command,
                ],
            )
        elif "powershell" in shell_name or "pwsh" in shell_name:
            subprocess.Popen(
                [shell, "-NoExit", "-Command", f"Set-Location '{working_dir}'; {command}"],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            subprocess.Popen(
                [shell, "/k", f"cd /d \"{working_dir}\" && {command}"],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        return True
    elif system == "Darwin":
        # macOS: use osascript to open Terminal.app
        apple_script = (
            f'tell application "Terminal" to do script '
            f'"cd {working_dir} && {command}"'
        )
        subprocess.Popen(["osascript", "-e", apple_script])
        return True

    # Linux / other: try common terminal emulators
    for term in ("gnome-terminal", "konsole", "xfce4-terminal", "xterm"):
        if _which(term):
            if term == "gnome-terminal":
                subprocess.Popen(
                    [term, "--title", title, "--working-directory", str(working_dir), "--", "bash", "-c", command],
                )
            else:
                subprocess.Popen(
                    [term, "-e", f"bash -c 'cd {working_dir} && {command}'"],
                )
            return True

    # No terminal emulator — fall back to background mode.
    return _launch_background(working_dir, command, label=title.split("–")[0].strip().lower())


def _can_open_terminal(system: str) -> bool:
    """Heuristic: can we spawn an interactive terminal window?"""
    if system in ("Windows", "Darwin"):
        return True
    if system == "Linux":
        # A running SSH session without a DISPLAY has no usable display,
        # so spawned terminal emulators would fail.
        if not os.environ.get("DISPLAY"):
            return False
        return any(_which(t) for t in ("gnome-terminal", "konsole", "xfce4-terminal", "xterm"))
    return True


def _launch_background(working_dir: Path, command: str, label: str | None = None) -> bool:
    """Start a dev process detached (headless) and log its output."""
    from llmport.core.workspace import dev_logs_dir, log_filename

    log_dir = dev_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / log_filename(label or os.path.basename(str(working_dir)))
    # Non-login shells (headless servers) don't source the profile, so
    # ~./local/bin (uv, taskiq extras) and SDK locations may be missing
    # from PATH. Prepend the usual suspects so the launched command
    # resolves the same tools an interactive shell would.
    is_windows = platform.system() == "Windows"
    candidates = [
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".cargo" / "bin"),
    ]
    if not is_windows:
        candidates += ["/opt/homebrew/bin", "/usr/local/bin"]
    extra_path = os.pathsep.join(filter(None, candidates))

    try:
        with open(log_file, "ab") as log:
            if is_windows:
                # Windows has no bash of its own, and the one Git ships cannot
                # cd into a native path -- a quoted C:\... is not a POSIX path.
                # Every headless service died on the script's first line with
                # "No such file or directory" and left a 100-byte log saying
                # so. Use the platform's own shell, and let Popen set the
                # directory instead of writing a cd at all.
                env = dict(os.environ)
                env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=str(working_dir),
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    # CREATE_NO_WINDOW, not DETACHED_PROCESS: both hide the
                    # console, but a detached process does not inherit the
                    # handles passed above, so every log file stayed empty --
                    # which defeats the whole point of headless mode.
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    ),
                )
            else:
                script = (
                    "set -e\n"
                    f"export PATH={shlex.quote(extra_path + os.pathsep)}${{PATH:-}}\n"
                    f"cd {shlex.quote(str(working_dir))}\n"
                    f"{command}\n"
                )
                proc = subprocess.Popen(
                    ["bash", "-c", script],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
    except OSError as exc:
        error(f"Could not start background process for {working_dir}: {exc}")
        return False
    info(f"{label or os.path.basename(str(working_dir))} (pid {proc.pid}) → {log_file}")
    return True


def _which(name: str) -> str | None:
    """Check if a tool is on PATH."""
    return shutil.which(name)


def _ensure_backend_env(backend_dir: Path, workspace: Path) -> None:
    """Create or repair ``llm_port_backend/.env``.

    Creates it with the dev defaults if missing. If it exists, the file
    is self-healed in place (all other lines preserved):

    * stale values for any known dev key (e.g. the per-service RabbitMQ
      user/pass from ``RABBITMQ_BACKEND_PASS`` — old files may carry the
      hard-coded ``guest/guest`` default that no longer exists on the
      broker, or a ``HOST`` pinning the server to loopback) are rewritten;
    * dev keys added in newer CLI versions that the file predates
      (``COOKIE_SECURE``, ``GATEWAY_URL``, …) are appended.
    """
    env_path = backend_dir / ".env"
    if not backend_dir.exists():
        return
    from llmport.core.env_gen import write_env_file
    from llmport.core.registry import backend_dev_env_for

    shared_env_path = _shared_env_path(workspace)
    if not shared_env_path:
        return
    desired = backend_dev_env_for(shared_env_path)

    if not env_path.exists():
        write_env_file(env_path, desired)
        info(f"Generated backend .env at {env_path}")
        return

    # Keys the CLI owns outright: a stale value gets rewritten in place.
    # (DB credentials are intentionally NOT here — a user-edited DB
    # pass in the file must survive across `dev up` runs.)
    rewrite = {
        k: v
        for k, v in desired.items()
        if k in ("LLM_PORT_BACKEND_HOST", "LLM_PORT_BACKEND_RABBIT_USER", "LLM_PORT_BACKEND_RABBIT_PASS")
    }
    # Keys added in newer CLI versions: appended if the file predates them.
    append = {
        k: v
        for k, v in desired.items()
        if k
        in (
            "LLM_PORT_BACKEND_COOKIE_SECURE",
            "LLM_PORT_BACKEND_GATEWAY_URL",
            "LLM_PORT_BACKEND_ENVIRONMENT",
        )
    }

    lines = env_path.read_text(encoding="utf-8").splitlines()
    current: dict[str, str] = {}
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            current[k.strip()] = v.strip()

    loopback = _loopback_fixes(current)
    rewrite.update(loopback)
    stale = [k for k, v in rewrite.items() if current.get(k) != v]
    missing = [
        k for k in rewrite if k not in current and k not in loopback
    ] + [k for k in append if k not in current]
    if not stale and not missing:
        return

    fixed: list[str] = []
    for line in lines:
        s = line.strip()
        k = s.split("=", 1)[0].strip() if s and not s.startswith("#") and "=" in s else None
        if k in stale:
            fixed.append(f"{k}={rewrite[k]}")  # rewrite stale value in place
        else:
            fixed.append(line)
    if missing:
        if fixed and fixed[-1].strip():
            fixed.append("")
        fixed.append("# ── llmport dev up: dev defaults ──")
        known = {**rewrite, **append}
        fixed.extend(f"{k}={known[k]}" for k in missing)
    env_path.write_text("\n".join(fixed) + "\n", encoding="utf-8")
    info("Backend .env healed to match the current dev defaults.")


def _loopback_fixes(current: dict[str, str]) -> dict[str, str]:
    """Infra host keys still set to "localhost", mapped to 127.0.0.1.

    Files written by older CLIs say "localhost", which costs a 21 s stall on
    every new connection on Windows (see ``INFRA_LOOPBACK``). Only that exact
    value is rewritten: a host someone pointed elsewhere is theirs.
    """
    from llmport.core.registry import INFRA_HOST_KEYS, INFRA_LOOPBACK

    return {
        k: INFRA_LOOPBACK
        for k in INFRA_HOST_KEYS
        if current.get(k, "").strip().lower() == "localhost"
    }


def _shared_env_path(workspace: Path) -> Path | None:
    """Locate the shared (infra) ``.env`` for the dev workspace."""
    for candidate in (
        workspace / "llm_port_shared" / ".env",
        workspace / "llm-port-core" / "llm_port_shared" / ".env",
    ):
        if candidate.exists():
            return candidate
    return None


def _ensure_shared_env(workspace: Path) -> Path | None:
    """Create or repair the shared (infra) ``.env``.

    ``dev down`` deletes the generated env files as part of a full reset,
    so a plain ``dev up`` must regenerate them before infra starts:

    * shared ``.env`` missing → write fresh dev credentials, then
      regenerate the RabbitMQ ``definitions.json`` from it (mirrors
      ``dev init`` step 2a + 2c; the RMQ broker comes up with the
      matching per-service users).
    * shared ``.env`` present → untouched (its DB/RMQ credentials must
      survive so the volumes they match are still usable; when the
      volumes were wiped, fresh containers self-seed from the env).

    Returns the shared env path (existing or newly written), or ``None``
    if the shared compose directory cannot be found.
    """
    from llmport.core.env_gen import dev_env_vars, write_env_file
    from llmport.core.workspace import resolve_shared_compose

    compose_file = resolve_shared_compose(workspace)
    if not compose_file:
        return None
    shared_dir = compose_file.parent
    env_path = shared_dir / ".env"
    if env_path.exists():
        return env_path

    info(f"Shared .env not found — generating fresh dev credentials at {env_path}")
    write_env_file(env_path, dev_env_vars())
    try:
        from llmport.commands.dev.dev_init import _resync_rmq_credentials

        _resync_rmq_credentials(shared_dir, skip_infra=False)
    except Exception as exc:  # noqa: BLE001 — resync is best-effort
        warning(f"RMQ definitions resync skipped ({exc}); broker users may need a manual restart.")
    return env_path


def _ensure_gateway_env(api_dir: Path, workspace: Path) -> None:
    """Create ``llm_port_api/.env`` for the host-running gateway.

    The gateway is the OpenAI-compatible ``/v1`` edge; in dev it runs as a
    host process (port 8001) and must point at the shared 127.0.0.1
    publish ports plus the host-local DB broker credentials from the
    shared ``.env``. Missing credential keys are preserved on re-runs
    (only absent keys are filled in) so a hand-edited file survives.
    """
    if not api_dir.exists():
        return
    from llmport.core.env_gen import write_env_file
    from llmport.core.registry import gateway_dev_env_for

    shared_env_path = _shared_env_path(workspace)
    if not shared_env_path:
        warning("Shared .env not found — gateway will start with default credentials.")
        return
    desired = gateway_dev_env_for(shared_env_path)
    env_path = api_dir / ".env"

    if not env_path.exists():
        write_env_file(env_path, desired)
        info(f"Generated gateway .env at {env_path}")
        return

    existing: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            existing[k.strip()] = v.strip()
    missing = {k: v for k, v in desired.items() if k not in existing}
    loopback = _loopback_fixes(existing)
    if loopback:
        lines = []
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            k = s.split("=", 1)[0].strip() if s and not s.startswith("#") and "=" in s else None
            lines.append(f"{k}={loopback[k]}" if k in loopback else line)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        info("Gateway .env: infra hosts moved from localhost to 127.0.0.1.")
    if not missing:
        return
    with env_path.open("a", encoding="utf-8") as fh:
        fh.write("# ── llmport dev up: gateway dev defaults ──\n")
        for k, v in missing.items():
            fh.write(f"{k}={v}\n")
    info("Gateway .env completed with missing dev defaults.")


def _apply_env(env_path: Path, values: dict[str, str], *, header: str) -> bool:
    """Set *values* in the ``.env`` at *env_path*, keeping every other line.

    A key already in the file is rewritten where it stands; a missing one is
    appended under *header*. Returns whether the file changed.
    """
    before = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    out: list[str] = []
    seen: set[str] = set()
    for line in before.splitlines():
        s = line.strip()
        key = s.split("=", 1)[0].strip() if s and not s.startswith("#") and "=" in s else None
        if key in values:
            seen.add(key)
            out.append(f"{key}={values[key]}")
        else:
            out.append(line)
    missing = [k for k in values if k not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append(f"# ── {header} ──")
        out.extend(f"{k}={values[k]}" for k in missing)
    after = "\n".join(out) + "\n"
    if after == before:
        return False
    env_path.write_text(after, encoding="utf-8")
    return True


def _ensure_module_envs(
    workspace: Path, backend_dir: Path, api_dir: Path, running: list[ModuleInfo],
) -> None:
    """Point each module, and the gateway and backend, at the modules that run.

    The switches in the gateway and backend ``.env`` are owned by ``dev up``:
    a module that is not started this time is switched off, not left pointing
    at a port nothing listens on.
    """
    from llmport.core.dev_modules import caller_env, module_env
    from llmport.core.registry import _read_env_values

    shared_path = _shared_env_path(workspace)
    shared = _read_env_values(shared_path) if shared_path else {}
    header = "llmport dev up: modules"
    if api_dir.exists() and _apply_env(api_dir / ".env", caller_env("API", shared=shared, running=running), header=header):
        info("Gateway .env: module switches updated.")
    if backend_dir.exists() and _apply_env(
        backend_dir / ".env", caller_env("BACKEND", shared=shared, running=running), header=header,
    ):
        info("Backend .env: module switches updated.")
    for module in running:
        module_dir = find_service_dir(workspace, module.dev_dir)
        if module_dir.exists():
            _apply_env(module_dir / ".env", module_env(module, shared=shared, running=running), header=header)


def _install_module_deps(module_dir: Path, module: ModuleInfo) -> None:
    """Sync a module's dependencies, and install what its lock cannot carry.

    ``--inexact``: an exact sync removes what the lock does not list, and the
    spaCy model PII needs is exactly that -- it would be uninstalled and
    downloaded again (~560 MB) on every ``dev up``.
    """
    label = _module_label(module)
    env = _own_environment()
    console.print(f"[cyan]Installing {label} dependencies (uv sync)…[/cyan]")
    result = subprocess.run(["uv", "sync", "--locked", "--inexact"], cwd=str(module_dir), env=env)
    if result.returncode != 0:
        warning("uv sync --locked failed, retrying without --locked…")
        result = subprocess.run(["uv", "sync", "--inexact"], cwd=str(module_dir), env=env)
        if result.returncode != 0:
            error(f"{label}: uv sync failed.")
            return
    for import_name, wheel in module.extra_wheels:
        probe = subprocess.run(
            ["uv", "run", "--no-sync", "python", "-c", f"import {import_name}"],
            cwd=str(module_dir), capture_output=True, env=env,
        )
        if probe.returncode == 0:
            continue
        console.print(f"[cyan]Installing {import_name} for {label} (one-time download)…[/cyan]")
        if subprocess.run(["uv", "pip", "install", wheel], cwd=str(module_dir), env=env).returncode != 0:
            error(f"{label}: could not install {import_name}; it will not start without it.")
    success(f"{label} dependencies installed.")


def _own_environment() -> dict[str, str]:
    """This environment, less the virtualenv ``dev up`` itself runs in.

    Run as ``uv run llmport``, the CLI's own ``.venv`` is ``VIRTUAL_ENV``.
    ``uv sync`` and ``uv run`` ignore it inside a project, but ``uv pip``
    honours it before the project's ``.venv``: PII's spaCy model was
    installed into the CLI, and PII could not load it.
    """
    return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def _migrate_module(module_dir: Path, module: ModuleInfo) -> None:
    """Bring a module's database up to date (its ``.env`` has the credentials)."""
    if not module.database or not (module_dir / "alembic.ini").exists():
        return
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"], cwd=str(module_dir), env=_own_environment(),
    )
    if result.returncode != 0:
        warning(f"{_module_label(module)}: Alembic migration exited with non-zero code.")
    else:
        success(f"{_module_label(module)} migrations up to date.")


def _module_label(module: ModuleInfo) -> str:
    return {"pii": "PII", "mcp": "MCP"}.get(module.name, module.name.capitalize())


def _module_present(workspace: Path, module: ModuleInfo) -> bool:
    """Whether the module's code is in this workspace; says so when it is not."""
    if find_service_dir(workspace, module.dev_dir).exists():
        return True
    warning(f"{_module_label(module)}: {module.dev_dir} is not in this workspace — not started.")
    return False


#: Ports the dev services bind, for the check after stopping them. The ports
#: of the modules started this time are added by ``dev up``.
_DEV_PORTS = {8000: "Backend", 8001: "API gateway", 5173: "Frontend"}


def _own_process_chain() -> set[int]:
    """This process and every ancestor of it.

    The reclaim matches on the workspace path, and so does the command line of
    whatever launched us -- ``uv run -m llmport`` from a shell inside the
    workspace. Without this the reclaim killed its own parent.
    """
    chain: set[int] = set()
    pid = os.getpid()
    for _ in range(12):  # a guard against a cycle, not a real depth limit
        if pid <= 0 or pid in chain:
            break
        chain.add(pid)
        parent = _parent_pid(pid)
        if parent is None:
            break
        pid = parent
    return chain


def _parent_pid(pid: int) -> int | None:
    """The parent of *pid*, or ``None`` when it cannot be determined."""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}')"
                 ".ParentProcessId"],
                capture_output=True, text=True, timeout=20,
            )
        except Exception:  # noqa: BLE001 - best effort
            return None
        text = result.stdout.strip()
        return int(text) if text.isdigit() else None

    try:
        # /proc/<pid>/stat: pid (comm) state ppid ... -- comm can contain
        # spaces and parentheses, so split after the last ')'.
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return int(stat[stat.rindex(")") + 1:].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _stop_workspace_services(workspace: Path) -> list[int]:
    """Stop every dev service already running out of *workspace*.

    Scoped by the workspace path rather than by process name: the point is to
    reclaim *this* checkout's services, not to kill anybody's unrelated
    Python. Every one of them -- backend, worker, gateway, frontend -- runs
    from an interpreter or a node_modules under the workspace, so the path is
    in the command line.

    Returns the pids stopped, for the caller to report.
    """
    if platform.system() != "Windows":
        return _stop_workspace_services_posix(workspace)

    # Get-Process has no CommandLine on PowerShell 5.1, so a Where-Object on
    # it matches nothing and the stop silently does nothing. CIM has it on
    # every version.
    script = (
        "$ws = '" + str(workspace).replace("'", "''") + "';"
        "Get-CimInstance Win32_Process |"
        " Where-Object { $_.CommandLine -and $_.CommandLine -like \"*$ws*\" -and"
        " ($_.Name -eq 'python.exe' -or $_.Name -eq 'node.exe' -or"
        "  $_.Name -eq 'taskiq.exe' -or $_.Name -eq 'uv.exe') } |"
        " ForEach-Object { $_.ProcessId }"
    )
    try:
        found = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:  # noqa: BLE001 - reclaiming is best effort
        return []

    pids = [int(line) for line in found.stdout.split() if line.strip().isdigit()]
    # Spare this command and everything that launched it.
    #
    # Sparing only our own pid was not enough: ``dev up`` runs as
    # ``uv run -m llmport``, so ``uv.exe`` is our parent and its command line
    # names the workspace just as a service's does. Killing it took the whole
    # terminal down, mid-reclaim, leaving nothing started and no explanation.
    own = _own_process_chain()
    pids = [pid for pid in pids if pid not in own]
    for pid in pids:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=30
        )
    return pids


def _stop_workspace_services_posix(workspace: Path) -> list[int]:
    """The same, through /proc-backed pgrep."""
    try:
        found = subprocess.run(
            ["pgrep", "-f", str(workspace)], capture_output=True, text=True, timeout=30
        )
    except Exception:  # noqa: BLE001 - pgrep is absent on some images
        return []

    own = _own_process_chain()
    pids = [
        int(line)
        for line in found.stdout.split()
        if line.strip().isdigit() and int(line) not in own
    ]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    return pids


#: How ``dev up`` invokes each service. Matched literally, so an ad-hoc shell
#: that merely mentions a module name is not mistaken for a running service.
_SERVICE_INVOCATIONS = (
    "uv run -m llm_port_backend",
    "uv run -m llm_port_api",
    "uv run taskiq worker",
    "npm run dev",
    "uv run -m llm_port_pii",
    "uv run -m llm_port_mcp",
    "uv run -m llm_port_skills",
)


def _is_within(path: Path, root: Path) -> bool:
    """Whether *path* is *root* or sits under it."""
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _stop_service_hosts(workspace: Path) -> list[int]:
    """Stop the terminals hosting this workspace's services.

    ``_stop_workspace_services`` matches the workspace path in a command
    line. The service processes satisfy that; the shells hosting them do
    not. A tab runs ``pwsh -NoExit -Command uv run -m llm_port_backend``,
    which names the module and no path at all, so the leaf process was
    killed and its host survived.

    That is what "the backend serves stale code" actually was. The host had
    inherited the listening socket, so the port stayed bound after its child
    died; the replacement could not bind, exited without saying anything,
    and the old code kept answering. Observed here as seven LISTEN entries
    on port 8000, every one owned by a pid that no longer existed.

    Matched on the exact invocation *and* a working directory inside the
    workspace, so a second checkout running the same module is left alone.
    """
    own = _own_process_chain()
    stopped: list[int] = []
    for proc in psutil.process_iter(["cmdline"]):
        if proc.pid in own:
            continue
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if not any(form in cmdline for form in _SERVICE_INVOCATIONS):
                continue
            inside = str(workspace) in cmdline or _is_within(Path(proc.cwd()), workspace)
        except (psutil.Error, OSError):
            continue
        if not inside:
            continue
        try:
            proc.kill()
            stopped.append(proc.pid)
        except psutil.Error:
            continue
    return stopped


def _ports_still_held() -> dict[int, str]:
    """Which dev ports are still listening, and what they are for.

    Checked after stopping, because "the port is free" is the only thing that
    actually predicts whether the service about to start will be the one
    serving. A process we did not recognise -- started by hand, or from
    another checkout -- holds the port just as well.
    """
    held: dict[int, str] = {}
    for port, name in _DEV_PORTS.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                held[port] = name
    return held


#: How multiprocessing re-execs a worker. The whole command line, on every
#: platform, is this import plus a handle -- no module name, no package, no
#: path to anything that would say which service it belongs to.
_MULTIPROCESSING_WORKER = "from multiprocessing.spawn import spawn_main"


def _stop_orphaned_workers() -> list[int]:
    """Stop multiprocessing workers whose supervisor is gone.

    uvicorn and taskiq spawn their workers through ``multiprocessing``. Stop
    the supervisor and those children survive, still holding the listening
    socket they inherited -- and then nothing can find them. The system
    connection table still attributes the socket to the supervisor, which no
    longer exists, and no live process reports holding it either. The port
    stays bound, and keeps serving the old build, while every tool that
    looks says nobody owns it.

    That is what "the backend is serving stale code" was. Twelve of these
    were answering on port 8000 with every parent long dead, and they came
    back on each restart because each restart made more.

    Matched on that command line *and* a parent that no longer exists: the
    workers of a running service have a living supervisor and are left alone.
    """
    own = _own_process_chain()
    stopped: list[int] = []
    for proc in psutil.process_iter(["cmdline", "ppid"]):
        if proc.pid in own:
            continue
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if _MULTIPROCESSING_WORKER not in cmdline:
                continue
            parent = proc.info.get("ppid")
            if parent and psutil.pid_exists(parent):
                continue  # its supervisor is alive; not ours to reap
            proc.kill()
        except (psutil.Error, OSError):
            continue
        stopped.append(proc.pid)
    return stopped


def _stop_port_holders(ports: dict[int, str]) -> list[int]:
    """Stop whatever still holds a dev port, whatever it looks like.

    Recognising a service by its name or path cannot be made to work.
    uvicorn and taskiq spawn their workers through ``multiprocessing``,
    which re-execs as ``python -c "from multiprocessing.spawn import
    spawn_main; ..."``: no module name, no workspace path, and on this
    machine not even the workspace's own interpreter -- the system one.
    Twelve such orphans were found serving port 8000 with every parent long
    dead, answering requests from another host, invisible to every filter
    written to look for them.

    So stop matching on what a process looks like. What matters is that it
    is sitting on the port the service about to start needs. Its own process
    chain is spared, as always.
    """
    own = _own_process_chain()
    stopped: list[int] = []
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError, PermissionError):
        return stopped

    holders: dict[int, set[int]] = {}
    for conn in connections:
        if conn.status != psutil.CONN_LISTEN or not conn.pid or not conn.laddr:
            continue
        if conn.laddr.port in ports:
            holders.setdefault(conn.laddr.port, set()).add(conn.pid)

    for port, pids in sorted(holders.items()):
        for pid in sorted(pids):
            if pid in own or pid <= 4:  # never the idle/system pids
                continue
            try:
                proc = psutil.Process(pid)
                name = proc.name()
            except psutil.Error:
                continue
            for victim in _supervisor_tree(proc, own):
                try:
                    victim.kill()
                    stopped.append(victim.pid)
                except psutil.Error:
                    continue
            warning(f"Stopped {name} (pid {pid}) still holding port {port} ({ports[port]}).")
    return stopped


#: Processes that exist to run another process. Walked through when looking
#: for the supervisor above a port holder.
_SUPERVISOR_NAMES = frozenset({
    "python.exe", "python", "python3", "uv.exe", "uv", "cmd.exe",
    "node.exe", "node", "taskiq.exe", "npm.cmd", "pwsh.exe", "powershell.exe",
    "sh", "bash",
})


def _supervisor_tree(proc: psutil.Process, own: set[int]) -> list[psutil.Process]:
    """*proc*, its supervisor, and everything under that -- children first.

    Killing the process that holds the port is not enough on its own. The
    backend runs under a reload supervisor, so removing the worker just makes
    the parent start another one, on the same port, and the reclaim reports
    failure against a pid that did not exist when it looked. Walk up to the
    top of the chain that exists only to run this service, then take the
    whole tree.

    The walk stops at anything outside that set, and at our own chain, so it
    never climbs out into the operator's session.
    """
    top = proc
    try:
        for parent in proc.parents():
            if parent.pid in own or parent.pid <= 4:
                break
            if parent.name().lower() not in _SUPERVISOR_NAMES:
                break
            top = parent
    except psutil.Error:
        pass

    try:
        # Children first: a supervisor that outlives its workers restarts them.
        tree = [*reversed(top.children(recursive=True)), top]
    except psutil.Error:
        tree = [top]
    return [p for p in tree if p.pid not in own and p.pid > 4]


def _reclaim_workspace(workspace: Path) -> None:
    """Stop this workspace's services and say what is still in the way."""
    stopped = _stop_workspace_services(workspace)
    # Then the shells hosting them, which keep the listening socket alive
    # after their child is gone.
    stopped += _stop_service_hosts(workspace)
    # And the workers those hosts left behind, which hold the port while
    # appearing to belong to nobody.
    stopped += _stop_orphaned_workers()
    if stopped:
        success(f"Stopped {len(stopped)} process(es) from a previous run.")

    # Sockets linger briefly after the process holding them dies.
    for _ in range(10):
        held = _ports_still_held()
        if not held:
            return
        time.sleep(0.5)

    # Still held: stop whatever is on the port, since by now it is not a
    # socket closing but a process that no filter above recognised.
    if _stop_port_holders(held):
        for _ in range(10):
            held = _ports_still_held()
            if not held:
                return
            time.sleep(0.5)

    for port, name in held.items():
        warning(
            f"Port {port} ({name}) is still in use and could not be stopped. "
            f"That process will keep serving, and the one started below will "
            f"exit quietly -- so what answers on {port} will not be this build."
        )


def _stop_old_workers() -> None:
    """Kill any stale taskiq worker processes."""
    if platform.system() != "Windows":
        # On Unix, use pkill
        subprocess.run(["pkill", "-f", "taskiq worker"], capture_output=True)
        return

    # Windows: use taskkill via pattern
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-Process python*, uv* -ErrorAction SilentlyContinue | "
             "Where-Object { $_.CommandLine -like '*taskiq*worker*' } | "
             "Stop-Process -Force -ErrorAction SilentlyContinue"],
            capture_output=True,
        )
    except Exception:
        pass


@dev_group.command("up")
@click.option("--backend-only", is_flag=True, help="Start only the backend.")
@click.option("--frontend-only", is_flag=True, help="Start only the frontend.")
@click.option("--skip-infra", is_flag=True, help="Skip shared infrastructure check.")
@click.option("--skip-deps", is_flag=True, help="Skip dependency installation.")
@click.option("--skip-migrations", is_flag=True, help="Skip Alembic migrations.")
@click.option(
    "--headless",
    is_flag=True,
    help="Run services as background processes with log files (for servers without a GUI).",
)
@click.option(
    "--modules",
    default=None,
    metavar="LIST",
    help=(
        "Optional modules to run beside the backend, comma-separated "
        "(pii, mcp, skills), or 'none'. Default: the ones switched on with "
        "`llmport module enable`."
    ),
)
@click.option(
    "--local-node",
    is_flag=True,
    help="Provision llm_port_node_agent locally or over SSH before launching dev services.",
)
@click.option(
    "--local-node-host",
    default="",
    help="SSH host for node-agent provisioning (example: ubuntu@10.0.0.12).",
)
@click.option(
    "--local-node-workdir",
    default="",
    help="Install directory for node-agent repo on target host.",
)
@click.option(
    "--local-node-branch",
    default="",
    help="Git branch for llm-port-node-agent (default: config dev.branch).",
)
@click.option(
    "--local-node-backend-url",
    default="http://127.0.0.1:8000",
    show_default=True,
    help="Backend URL written to node-agent environment.",
)
@click.option(
    "--local-node-advertise-host",
    default="",
    help="Host/IP that node agent advertises for runtime endpoints.",
)
@click.option(
    "--local-node-enrollment-token",
    default="",
    help="Optional one-time enrollment token for initial node onboarding.",
)
@click.option(
    "--local-node-sudo/--local-node-no-sudo",
    default=True,
    show_default=True,
    help="Use sudo for systemd installation in node-agent provisioning.",
)
def dev_up(
    *,
    backend_only: bool,
    frontend_only: bool,
    skip_infra: bool,
    skip_deps: bool,
    skip_migrations: bool,
    headless: bool,
    modules: str | None,
    local_node: bool,
    local_node_host: str,
    local_node_workdir: str,
    local_node_branch: str,
    local_node_backend_url: str,
    local_node_advertise_host: str,
    local_node_enrollment_token: str,
    local_node_sudo: bool,
) -> None:
    """Start backend, worker, and frontend dev servers.

    Each service launches in its own terminal window, or as a
    background process with a log file when no GUI is available
    (headless Linux server) — pass ``--headless`` to force that
    mode. Mirrors the behaviour of the existing start-dev.ps1 script.

    \b
    Services started:
      • Backend   → uv run -m llm_port_backend  (http://localhost:8000)
      • Gateway   → uv run -m llm_port_api      (http://localhost:8001, /v1 edge)
      • Worker    → uv run taskiq worker …       (task processing)
      • Frontend  → npm run dev                  (http://localhost:5173)

    With --modules (or modules switched on with `llmport module enable`):
      • PII       → uv run -m llm_port_pii       (http://127.0.0.1:8003)
      • MCP       → uv run -m llm_port_mcp       (http://127.0.0.1:8007)
      • Skills    → uv run -m llm_port_skills    (http://127.0.0.1:8008)
    """
    from llmport.core.dev_modules import UnknownModuleError, select_modules

    cfg = load_config()
    workspace = _find_workspace()
    # Monorepo-aware: init clones services into <workspace>/llm-port-core/…
    backend_dir = find_service_dir(workspace, "llm_port_backend")
    frontend_dir = find_service_dir(workspace, "llm_port_frontend")
    api_dir = find_service_dir(workspace, "llm_port_api")

    try:
        running_modules = [] if frontend_only else select_modules(modules, cfg.profiles)
    except UnknownModuleError as exc:
        error(str(exc))
        sys.exit(2)
    running_modules = [
        m for m in running_modules if _module_present(workspace, m)
    ]

    console.print("[bold magenta]llm.port Dev Environment[/bold magenta]\n")

    # ── Self-heal the shared (infra) .env ─────────────────────────
    # `dev down` is a full reset and deletes the generated env files.
    # Without this, a plain `dev up` afterwards would start infra with
    # default credentials while the RMQ container (fresh volume) has no
    # users at all, and backend/gateway would get ACCESS_REFUSED.
    if not frontend_only:
        _ensure_shared_env(workspace)

    # ── Ensure backend / gateway .env exist ───────────────────────
    _ensure_backend_env(backend_dir, workspace)
    if not frontend_only:
        _ensure_gateway_env(api_dir, workspace)
        _ensure_module_envs(workspace, backend_dir, api_dir, running_modules)

    # ── Shared infra ──────────────────────────────────────────────
    if not skip_infra and not frontend_only:
        from llmport.commands.dev.dev_init import _wait_for_postgres
        from llmport.core.compose import ComposeContext, up as compose_up
        from llmport.core.registry import INFRA_SERVICES

        console.print("[cyan]Checking shared infrastructure…[/cyan]")
        compose_file = resolve_shared_compose(workspace)
        if compose_file:
            env_file = compose_file.parent / ".env"
            ctx = ComposeContext(
                compose_files=[str(compose_file)],
                env_file=str(env_file) if env_file.exists() else None,
                project_dir=str(compose_file.parent),
            )
            rc = compose_up(ctx, services=INFRA_SERVICES, detach=True)
            if rc != 0:
                warning("Shared infra compose up reported failures (see output above).")
            if _wait_for_postgres(timeout=30):
                success("Shared infrastructure running.")
                _ensure_databases(backend_dir)
            else:
                warning("Postgres did not become ready. Continuing anyway…")
        else:
            warning("Shared compose file not found. Skipping infra check.")

    # ── Dependencies ──────────────────────────────────────────────
    if not skip_deps:
        if not frontend_only and backend_dir.exists():
            from llmport.commands.dev.dev_init import _install_backend_deps
            _install_backend_deps(backend_dir)
        if not backend_only and frontend_dir.exists():
            from llmport.commands.dev.dev_init import _install_frontend_deps
            _install_frontend_deps(frontend_dir)
        if not frontend_only and api_dir.exists():
            from llmport.commands.dev.dev_init import _install_backend_deps
            _install_backend_deps(api_dir)
        for module in running_modules:
            _install_module_deps(find_service_dir(workspace, module.dev_dir), module)

    # ── Migrations ────────────────────────────────────────────────
    if not skip_migrations and not frontend_only and backend_dir.exists():
        from llmport.commands.dev.dev_init import _run_migrations
        _run_migrations(backend_dir)
    if not skip_migrations:
        for module in running_modules:
            _migrate_module(find_service_dir(workspace, module.dev_dir), module)

    # ── Reclaim the workspace ─────────────────────────────────────
    # Before anything is launched, not after: a service that is already
    # running keeps its port, so the one started below exits immediately and
    # the old one carries on serving. Nothing about that looks like a
    # failure -- ``dev up`` reports success, the logs show a clean start, and
    # the running system quietly ignores every change made since.
    console.print("\n[cyan]Stopping anything already running here…[/cyan]")
    # The ports of the modules about to start too -- and only theirs: a port
    # of a module not started here may be someone else's.
    _DEV_PORTS.update({m.port: _module_label(m) for m in running_modules})
    _reclaim_workspace(workspace)

    started: list[str] = []

    # ── Launch modules ────────────────────────────────────────────
    # First, so they are loading (PII reads a 560 MB model) while the rest
    # start. Nothing waits for them: the gateway calls them per request.
    for module in running_modules:
        label = _module_label(module)
        module_dir = find_service_dir(workspace, module.dev_dir)
        console.print(f"\n[cyan]Launching {label}…[/cyan]")
        if _launch_terminal(label, module_dir, f"uv run -m {module.dev_dir}", headless=headless):
            success(f"{label} → http://127.0.0.1:{module.port}")
            started.append(label)
        else:
            error(f"{label} failed to start.")

    # ── Launch backend ────────────────────────────────────────────
    if not frontend_only:
        if not backend_dir.exists():
            error(f"Backend directory not found: {backend_dir}")
        else:
            console.print("\n[cyan]Launching backend…[/cyan]")
            if _launch_terminal("Backend", backend_dir, "uv run -m llm_port_backend", headless=headless):
                success("Backend server → http://localhost:8000")
                started.append("Backend")
            else:
                error("Backend failed to start.")

            # Taskiq worker
            _stop_old_workers()
            console.print("[cyan]Launching taskiq worker…[/cyan]")
            if _launch_terminal(
                "Worker", backend_dir,
                "uv run taskiq worker llm_port_backend.tkq:broker llm_port_backend.services.llm.tasks llm_port_backend.services.rag_lite.tasks",
                headless=headless,
            ):
                success("Taskiq worker started.")
                started.append("Worker")
            else:
                error("Taskiq worker failed to start.")

            # API gateway (OpenAI-compatible /v1 edge)
            if not api_dir.exists():
                warning(f"API gateway directory not found: {api_dir} — skipping.")
            else:
                console.print("[cyan]Launching API gateway…[/cyan]")
                if _launch_terminal("API Gateway", api_dir, "uv run -m llm_port_api", headless=headless):
                    success("API gateway → http://localhost:8001")
                    started.append("API gateway")
                else:
                    error("API gateway failed to start.")

    # ── Launch frontend ───────────────────────────────────────────
    if not backend_only:
        if not frontend_dir.exists():
            error(f"Frontend directory not found: {frontend_dir}")
        else:
            console.print("\n[cyan]Launching frontend…[/cyan]")
            if _launch_terminal("Frontend", frontend_dir, "npm run dev", headless=headless):
                success("Frontend server → http://localhost:5173")
                started.append("Frontend")
            else:
                error("Frontend failed to start.")

    # ── Optional local-node provisioning (after backend is up) ───
    if local_node:
        from llmport.core.local_node import (  # noqa: PLC0415
            create_enrollment_token,
            provision_local_node_agent,
        )

        enrollment_token = local_node_enrollment_token

        # Auto-create token if none provided: wait for backend,
        # then bootstrap (idempotent — skips if already done) to
        # obtain an API token and create an enrollment token.
        if not enrollment_token.strip():
            from llmport.core.bootstrap import (  # noqa: PLC0415
                bootstrap_interactive,
                wait_for_backend,
            )

            dev_backend_url = local_node_backend_url.strip() or "http://localhost:8000"
            console.print("  [dim]Waiting for backend to become healthy…[/dim]")
            if wait_for_backend(dev_backend_url, timeout=60):
                shared_dir = workspace / "llm_port_shared"
                if not shared_dir.is_dir():
                    shared_dir = workspace / "llm-port-core" / "llm_port_shared"
                creds = bootstrap_interactive(
                    dev_backend_url,
                    shared_dir,
                    auto_confirm=True,
                )
                if creds and creds.get("api_token"):
                    info("No enrollment token provided — creating one automatically…")
                    enrollment_token = create_enrollment_token(dev_backend_url, creds["api_token"]) or ""
                elif not creds:
                    # Already bootstrapped — try reading saved credentials
                    creds_file = shared_dir / ".bootstrap-credentials"
                    if creds_file.exists():
                        for line in creds_file.read_text(encoding="utf-8").splitlines():
                            if line.startswith("API_TOKEN="):
                                api_token = line.split("=", 1)[1].strip()
                                if api_token:
                                    info("Using saved API token to create enrollment token…")
                                    enrollment_token = create_enrollment_token(dev_backend_url, api_token) or ""
                                break
            else:
                warning("Backend not healthy — cannot auto-create enrollment token.")

            if not enrollment_token.strip():
                warning(
                    "No enrollment token available. Provide one with"
                    " --local-node-enrollment-token."
                )

        branch = local_node_branch.strip() or (cfg.dev.branch if cfg.dev and cfg.dev.branch else "master")
        remote_host = local_node_host.strip() or None
        method = cfg.dev.clone_method if cfg.dev and cfg.dev.clone_method else "https"
        github_token = cfg.dev.github_token if cfg.dev else ""

        ok = provision_local_node_agent(
            workspace=workspace,
            branch=branch,
            backend_url=local_node_backend_url,
            advertise_host=local_node_advertise_host,
            enrollment_token=enrollment_token,
            remote_host=remote_host,
            use_sudo=local_node_sudo,
            method=method,
            github_token=github_token,
            workdir_override=local_node_workdir.strip() or None,
        )
        if not ok:
            error("Local-node provisioning failed.")
            sys.exit(1)

    # ── Summary ───────────────────────────────────────────────────
    if started:
        console.print("\n[bold green]Dev environment started![/bold green]")
        from llmport.core.workspace import dev_logs_dir

        if headless or platform.system() == "Linux":
            log_dir = dev_logs_dir()
            console.print(f"[dim]Background logs: {log_dir}/ (pids in log filenames)\n[/dim]")
            console.print("[dim]Stop services: llmport dev down  (--keep-infra to keep containers; --volumes to also wipe dev data)[/dim]")
        else:
            console.print("[dim]Each service runs in its own terminal window.[/dim]")
            console.print("[dim]Close windows or Ctrl+C to stop.[/dim]")
    else:
        console.print("\n[bold red]No dev services started.[/bold red]")
        if headless:
            console.print("[dim]Check the errors above (e.g. missing deps) and re-run.[/dim]")
        console.print("[dim]You can start individual services manually from the workspace[/dim]")
        sys.exit(1)

    endpoints = []
    module_names = {m.name for m in running_modules}
    for name, url in DEV_ENDPOINTS:
        if frontend_only and name in ("Backend", "API Docs", "Worker", "LLM API"):
            continue
        if backend_only and name == "Frontend":
            continue
        module_name = DEV_MODULE_ENDPOINTS.get(name)
        if module_name is not None and module_name not in module_names:
            continue  # a module that is not running
        endpoints.append((name, url))

    console.print()
    for name, url in endpoints:
        console.print(f"  [bold]{name:12s}[/bold]  {url}")
    console.print()
