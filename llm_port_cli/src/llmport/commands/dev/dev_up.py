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
import subprocess
import sys
from pathlib import Path

import click

from llmport.core.console import console, success, warning, error, info
from llmport.core.registry import BACKEND_DEV_ENV, DEV_ENDPOINTS
from llmport.core.settings import load_config

from .dev_group import dev_group


def _find_workspace() -> Path:
    """Resolve the dev workspace from config or cwd."""
    cfg = load_config()
    if cfg.dev and cfg.dev.workspace_dir:
        return Path(cfg.dev.workspace_dir)
    return Path.cwd()


def _launch_terminal(title: str, working_dir: Path, command: str) -> None:
    """Spawn a new terminal window for a dev process."""
    system = platform.system()

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
    elif system == "Darwin":
        # macOS: use osascript to open Terminal.app
        apple_script = (
            f'tell application "Terminal" to do script '
            f'"cd {working_dir} && {command}"'
        )
        subprocess.Popen(["osascript", "-e", apple_script])
    else:
        # Linux: try common terminal emulators
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
                return
        warning(f"Could not find a terminal emulator. Run manually:\n  cd {working_dir} && {command}")


def _which(name: str) -> str | None:
    """Check if a tool is on PATH."""
    import shutil
    return shutil.which(name)


def _ensure_backend_env(backend_dir: Path) -> None:
    """Create llm_port_backend/.env with localhost settings if missing."""
    env_path = backend_dir / ".env"
    if env_path.exists() or not backend_dir.exists():
        return
    from llmport.core.env_gen import write_env_file
    write_env_file(env_path, dict(BACKEND_DEV_ENV))
    info(f"Generated backend .env at {env_path}")


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

    Each service launches in its own terminal window. Mirrors the
    behaviour of the existing start-dev.ps1 script.

    \b
    Services started:
      • Backend   → uv run -m llm_port_backend  (http://localhost:8000)
      • Worker    → uv run taskiq worker …       (task processing)
      • Frontend  → npm run dev                  (http://localhost:5173)
    """
    cfg = load_config()
    workspace = _find_workspace()
    backend_dir = workspace / "llm_port_backend"
    frontend_dir = workspace / "llm_port_frontend"

    console.print("[bold magenta]llm.port Dev Environment[/bold magenta]\n")

    # ── Ensure backend .env exists ────────────────────────────────
    _ensure_backend_env(backend_dir)

    # ── Shared infra ──────────────────────────────────────────────
    if not skip_infra and not frontend_only:
        from llmport.commands.dev.dev_init import _resolve_shared_compose, _wait_for_postgres
        from llmport.core.compose import ComposeContext, up as compose_up
        from llmport.core.registry import INFRA_SERVICES

        console.print("[cyan]Checking shared infrastructure…[/cyan]")
        compose_file = _resolve_shared_compose(workspace)
        if compose_file:
            env_file = compose_file.parent / ".env"
            ctx = ComposeContext(
                compose_files=[str(compose_file)],
                env_file=str(env_file) if env_file.exists() else None,
                project_dir=str(compose_file.parent),
            )
            compose_up(ctx, services=INFRA_SERVICES, detach=True)
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

    # ── Migrations ────────────────────────────────────────────────
    if not skip_migrations and not frontend_only and backend_dir.exists():
        from llmport.commands.dev.dev_init import _run_migrations
        _run_migrations(backend_dir)

    # ── Launch backend ────────────────────────────────────────────
    if not frontend_only:
        if not backend_dir.exists():
            error(f"Backend directory not found: {backend_dir}")
        else:
            console.print("\n[cyan]Launching backend…[/cyan]")
            _launch_terminal(
                "Backend – llm-port",
                backend_dir,
                "uv run -m llm_port_backend",
            )
            success("Backend server → http://localhost:8000")

            # Taskiq worker
            _stop_old_workers()
            console.print("[cyan]Launching taskiq worker…[/cyan]")
            _launch_terminal(
                "Worker – llm-port",
                backend_dir,
                "uv run taskiq worker llm_port_backend.tkq:broker llm_port_backend.services.llm.tasks llm_port_backend.services.rag_lite.tasks",
            )
            success("Taskiq worker started.")

    # ── Launch frontend ───────────────────────────────────────────
    if not backend_only:
        if not frontend_dir.exists():
            error(f"Frontend directory not found: {frontend_dir}")
        else:
            console.print("\n[cyan]Launching frontend…[/cyan]")
            _launch_terminal(
                "Frontend – llm-port",
                frontend_dir,
                "npm run dev",
            )
            success("Frontend server → http://localhost:5173")

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
    console.print("\n[bold green]Dev environment started![/bold green]")
    console.print("[dim]Each service runs in its own terminal window.[/dim]")
    console.print("[dim]Close windows or Ctrl+C to stop.[/dim]")

    endpoints = []
    for name, url in DEV_ENDPOINTS:
        if frontend_only and name in ("Backend", "API Docs", "Worker"):
            continue
        if backend_only and name == "Frontend":
            continue
        endpoints.append((name, url))

    console.print()
    for name, url in endpoints:
        console.print(f"  [bold]{name:12s}[/bold]  {url}")
    console.print()
