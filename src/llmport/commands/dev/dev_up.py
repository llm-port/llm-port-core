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
import os
import subprocess
import sys
from pathlib import Path

import click

from llmport.core.console import console, success, warning, error, info
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
    env = {
        "LLM_PORT_BACKEND_HOST": "localhost",
        "LLM_PORT_BACKEND_PORT": "8000",
        "LLM_PORT_BACKEND_RELOAD": "true",
        "LLM_PORT_BACKEND_DB_HOST": "localhost",
        "LLM_PORT_BACKEND_DB_PORT": "5432",
        "LLM_PORT_BACKEND_DB_USER": "llm_port_backend",
        "LLM_PORT_BACKEND_DB_PASS": "llm_port_backend",
        "LLM_PORT_BACKEND_DB_BASE": "llm_port_backend",
        "LLM_PORT_BACKEND_RABBIT_HOST": "localhost",
        "LLM_PORT_BACKEND_RABBIT_PORT": "5672",
        "LLM_PORT_BACKEND_RABBIT_USER": "guest",
        "LLM_PORT_BACKEND_RABBIT_PASS": "guest",
        "LLM_PORT_BACKEND_RABBIT_VHOST": "/",
    }
    write_env_file(env_path, env)
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
def dev_up(
    *,
    backend_only: bool,
    frontend_only: bool,
    skip_infra: bool,
    skip_deps: bool,
    skip_migrations: bool,
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

        console.print("[cyan]Checking shared infrastructure…[/cyan]")
        compose_file = _resolve_shared_compose(workspace)
        if compose_file:
            env_file = compose_file.parent / ".env"
            ctx = ComposeContext(
                compose_files=[str(compose_file)],
                env_file=str(env_file) if env_file.exists() else None,
                project_dir=str(compose_file.parent),
            )
            compose_up(ctx, detach=True)
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
                "uv run taskiq worker llm_port_backend.tkq:broker llm_port_backend.services.llm.tasks",
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

    # ── Summary ───────────────────────────────────────────────────
    console.print("\n[bold green]Dev environment started![/bold green]")
    console.print("[dim]Each service runs in its own terminal window.[/dim]")
    console.print("[dim]Close windows or Ctrl+C to stop.[/dim]")

    endpoints = []
    if not frontend_only:
        endpoints += [
            ("Backend", "http://localhost:8000"),
            ("API Docs", "http://localhost:8000/api/docs"),
            ("Worker", "Taskiq (RabbitMQ)"),
        ]
    if not backend_only:
        endpoints += [("Frontend", "http://localhost:5173")]
    endpoints += [
        ("Grafana", "http://localhost:3001"),
        ("pgAdmin", "http://localhost:5050"),
        ("RabbitMQ", "http://localhost:15672"),
        ("LLM API", "http://localhost:8001"),
        ("RAG API", "http://localhost:8002"),
    ]

    console.print()
    for name, url in endpoints:
        console.print(f"  [bold]{name:12s}[/bold]  {url}")
    console.print()
