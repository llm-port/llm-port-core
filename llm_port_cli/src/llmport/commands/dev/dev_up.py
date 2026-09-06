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
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import click

from llmport.core.console import console, success, warning, error, info
from llmport.core.registry import DEV_ENDPOINTS
from llmport.core.settings import load_config
from llmport.core.workspace import find_service_dir, resolve_shared_compose

from .dev_group import dev_group


def _find_workspace() -> Path:
    """Resolve the dev workspace from config or cwd."""
    cfg = load_config()
    if cfg.dev and cfg.dev.workspace_dir:
        return Path(cfg.dev.workspace_dir)
    return Path.cwd()


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
            subprocess.Popen(
                [
                    wt, "new-tab",
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
    extra_path = os.pathsep.join(
        filter(
            None,
            [
                str(Path.home() / ".local" / "bin"),
                str(Path.home() / ".cargo" / "bin"),
                "/opt/homebrew/bin",
                "/usr/local/bin",
            ],
        )
    )
    script = f"set -e\nexport PATH={shlex.quote(extra_path + os.pathsep) }${{PATH:-}}\ncd {shlex.quote(str(working_dir))}\n{command}\n"
    try:
        with open(log_file, "ab") as log:
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
        if k in ("LLM_PORT_BACKEND_COOKIE_SECURE", "LLM_PORT_BACKEND_GATEWAY_URL")
    }

    lines = env_path.read_text(encoding="utf-8").splitlines()
    current: dict[str, str] = {}
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            current[k.strip()] = v.strip()

    stale = [k for k, v in rewrite.items() if current.get(k) != v]
    missing = [k for k in rewrite if k not in current] + [k for k in append if k not in current]
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
    if not missing:
        return
    with env_path.open("a", encoding="utf-8") as fh:
        fh.write("# ── llmport dev up: gateway dev defaults ──\n")
        for k, v in missing.items():
            fh.write(f"{k}={v}\n")
    info("Gateway .env completed with missing dev defaults.")


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
    """
    cfg = load_config()
    workspace = _find_workspace()
    # Monorepo-aware: init clones services into <workspace>/llm-port-core/…
    backend_dir = find_service_dir(workspace, "llm_port_backend")
    frontend_dir = find_service_dir(workspace, "llm_port_frontend")
    api_dir = find_service_dir(workspace, "llm_port_api")

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

    # ── Migrations ────────────────────────────────────────────────
    if not skip_migrations and not frontend_only and backend_dir.exists():
        from llmport.commands.dev.dev_init import _run_migrations
        _run_migrations(backend_dir)

    started: list[str] = []

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
    for name, url in DEV_ENDPOINTS:
        if frontend_only and name in ("Backend", "API Docs", "Worker", "LLM API"):
            continue
        if backend_only and name == "Frontend":
            continue
        endpoints.append((name, url))

    console.print()
    for name, url in endpoints:
        console.print(f"  [bold]{name:12s}[/bold]  {url}")
    console.print()
