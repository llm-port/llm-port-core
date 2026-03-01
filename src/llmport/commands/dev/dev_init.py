"""``llmport dev init`` — bootstrap a full development workspace.

Clones all repositories, installs dependencies, starts shared
infrastructure, runs migrations, and generates a VS Code workspace file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click

from llmport.core.compose import ComposeContext, up as compose_up
from llmport.core.console import console, success, warning, error, info
from llmport.core.detect import detect_docker, check_tool
from llmport.core.env_gen import dev_env_vars, write_env_file
from llmport.core.git import clone_all_repos
from llmport.core.settings import (
    DevConfig,
    LlmportConfig,
    REPO_DIR_MAP,
    load_config,
    save_config,
)

from .dev_group import dev_group


def _resolve_shared_compose(workspace: Path) -> Path | None:
    """Locate the shared docker-compose file."""
    for candidate in (
        workspace / "llm_port_shared" / "docker-compose.yaml",
        workspace / "llm_port_shared" / "docker-compose.yml",
        workspace / "infra" / "shared" / "docker-compose.yaml",
        workspace / "infra" / "shared" / "docker-compose.yml",
    ):
        if candidate.exists():
            return candidate
    return None


def _wait_for_postgres(container: str = "llm-port-postgres", timeout: int = 60) -> bool:
    """Block until the Postgres container reports healthy or running."""
    import time

    waited = 0
    while waited < timeout:
        try:
            result = subprocess.run(
                [
                    "docker", "inspect", "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}running{{end}}",
                    container,
                ],
                capture_output=True,
                text=True,
            )
            status = result.stdout.strip()
            if status in ("healthy", "running"):
                return True
        except Exception:
            pass
        time.sleep(3)
        waited += 3
    return False


def _ensure_database(db_name: str, pg_user: str = "postgres") -> None:
    """Ensure a database exists inside the shared Postgres container."""
    check_sql = f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"
    result = subprocess.run(
        ["docker", "exec", "llm-port-postgres", "psql", "-U", pg_user, "-tAc", check_sql],
        capture_output=True,
        text=True,
    )
    if "1" in (result.stdout or ""):
        info(f"Database '{db_name}' already exists.")
        return

    subprocess.run(
        ["docker", "exec", "llm-port-postgres", "psql", "-U", pg_user, "-c", f"CREATE DATABASE {db_name};"],
        capture_output=True,
        text=True,
    )
    success(f"Created database '{db_name}'.")


def _install_backend_deps(backend_dir: Path) -> None:
    """Run ``uv sync`` in the backend directory."""
    console.print("[cyan]Installing backend dependencies (uv sync)…[/cyan]")
    result = subprocess.run(["uv", "sync", "--locked"], cwd=str(backend_dir))
    if result.returncode != 0:
        warning("uv sync --locked failed, retrying without --locked…")
        result = subprocess.run(["uv", "sync"], cwd=str(backend_dir))
        if result.returncode != 0:
            error("uv sync failed.")
            return
    success("Backend dependencies installed.")


def _install_frontend_deps(frontend_dir: Path) -> None:
    """Run ``npm install`` in the frontend directory."""
    console.print("[cyan]Installing frontend dependencies (npm install)…[/cyan]")
    result = subprocess.run(["npm", "install"], cwd=str(frontend_dir), shell=True)
    if result.returncode != 0:
        error("npm install failed.")
        return
    success("Frontend dependencies installed.")


def _run_migrations(backend_dir: Path) -> None:
    """Run Alembic migrations."""
    console.print("[cyan]Running database migrations…[/cyan]")
    env = os.environ.copy()
    # Ensure backend Alembic connects with the right credentials.
    env.setdefault("LLM_PORT_BACKEND_DB_HOST", "localhost")
    env.setdefault("LLM_PORT_BACKEND_DB_PORT", "5432")
    env.setdefault("LLM_PORT_BACKEND_DB_USER", "llm_port_backend")
    env.setdefault("LLM_PORT_BACKEND_DB_PASS", "llm_port_backend")
    env.setdefault("LLM_PORT_BACKEND_DB_BASE", "llm_port_backend")
    result = subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=str(backend_dir), env=env)
    if result.returncode != 0:
        warning("Alembic migration exited with non-zero code.")
    else:
        success("Migrations up to date.")


def _generate_vscode_workspace(workspace: Path) -> None:
    """Create a VS Code multi-root workspace file."""
    workspace_file = workspace / "llm-port.code-workspace"

    folders = []
    for _gh_name, local_name in sorted(REPO_DIR_MAP.items()):
        repo_path = workspace / local_name
        if repo_path.exists():
            folders.append({"path": local_name})

    content = {
        "folders": folders,
        "settings": {
            "python.defaultInterpreterPath": ".venv/bin/python",
            "editor.formatOnSave": True,
            "[python]": {
                "editor.defaultFormatter": "charliermarsh.ruff",
            },
            "[typescript]": {
                "editor.defaultFormatter": "esbenp.prettier-vscode",
            },
            "[typescriptreact]": {
                "editor.defaultFormatter": "esbenp.prettier-vscode",
            },
        },
    }
    workspace_file.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    success(f"VS Code workspace file: {workspace_file}")


@dev_group.command("init")
@click.argument("workspace", type=click.Path(), default=".")
@click.option("--ssh", is_flag=True, help="Clone using SSH instead of HTTPS.")
@click.option("--branch", "-b", default="master", show_default=True, help="Branch to checkout after cloning.")
@click.option("--overwrite", is_flag=True, help="Remove and re-clone existing repos.")
@click.option("--skip-infra", is_flag=True, help="Skip starting shared infrastructure.")
@click.option("--skip-deps", is_flag=True, help="Skip installing dependencies.")
@click.option("--skip-migrations", is_flag=True, help="Skip running database migrations.")
def dev_init(
    workspace: str,
    *,
    ssh: bool,
    branch: str,
    overwrite: bool,
    skip_infra: bool,
    skip_deps: bool,
    skip_migrations: bool,
) -> None:
    """Bootstrap a full llm.port development workspace.

    Clones all repositories, installs dependencies, starts shared
    infrastructure, runs migrations, and generates a VS Code workspace.

    \b
    Steps:
      1. Clone all llm.port repositories
      2. Generate .env with dev credentials
      3. Start shared infrastructure (Postgres, Redis, RabbitMQ, …)
      4. Ensure databases exist
      5. Install backend (uv) and frontend (npm) dependencies
      6. Run Alembic migrations
      7. Generate VS Code workspace file

    Example:

        llmport dev init --workspace ~/projects/llm-port --branch feature/my-work
    """
    workspace_path = Path(workspace).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold magenta]llm.port Developer Workspace Setup[/bold magenta]")
    console.print(f"[dim]Workspace: {workspace_path}[/dim]\n")

    # ── Prerequisites ─────────────────────────────────────────────
    console.print("[bold cyan]Checking prerequisites…[/bold cyan]")
    docker_info = detect_docker()
    if not docker_info.installed:
        error(
            "Either Docker is not installed or Docker engine is not running.\n"
            "  Install: https://docs.docker.com/desktop/\n"
            "  If already installed, make sure Docker Desktop is running."
        )
        sys.exit(1)
    if not docker_info.daemon_running:
        error("Docker daemon is not running. Start Docker Desktop.")
        sys.exit(1)

    git_check = check_tool("git")
    if not git_check.found:
        error("Git is required.")
        sys.exit(1)

    success("Prerequisites OK.")

    # ── 1. Clone repos ────────────────────────────────────────────
    console.print("\n[bold cyan]Step 1: Cloning repositories…[/bold cyan]")
    clone_method = "ssh" if ssh else "https"
    results = clone_all_repos(list(REPO_DIR_MAP.keys()), target_dir=workspace_path, method=clone_method, branch=branch, force=overwrite)

    cloned = sum(1 for r in results if r.cloned)
    skipped = sum(1 for r in results if r.skipped)
    errors = [r for r in results if r.error]
    info(f"Cloned: {cloned}, Skipped (already exist): {skipped}, Errors: {len(errors)}")
    for r in errors:
        error(f"  {r.repo}: {r.error}")

    # ── 2. Generate .env ──────────────────────────────────────────
    console.print("\n[bold cyan]Step 2: Generating .env file…[/bold cyan]")
    shared_dir = workspace_path / "llm_port_shared"
    env_path = shared_dir / ".env"
    if env_path.exists():
        warning(f".env already exists at {env_path} — skipping.")
    else:
        env_vars = dev_env_vars(profiles=[])
        write_env_file(env_path, env_vars)
        success(f".env written to {env_path}")

    # ── 3. Start shared infrastructure ────────────────────────────
    if not skip_infra:
        console.print("\n[bold cyan]Step 3: Starting shared infrastructure…[/bold cyan]")
        compose_file = _resolve_shared_compose(workspace_path)
        if compose_file:
            ctx = ComposeContext(
                compose_files=[str(compose_file)],
                env_file=str(env_path) if env_path.exists() else None,
                project_dir=str(compose_file.parent),
            )
            returncode = compose_up(ctx, detach=True)
            if returncode != 0:
                error("docker compose up failed for shared infrastructure.")
            else:
                console.print("[cyan]Waiting for Postgres…[/cyan]")
                if _wait_for_postgres():
                    success("Shared infrastructure is running.")
                else:
                    warning("Postgres did not become ready in time.")
        else:
            warning("Could not find shared compose file. Skipping infrastructure startup.")
    else:
        info("Skipping infrastructure startup (--skip-infra).")

    # ── 4. Ensure databases ───────────────────────────────────────
    if not skip_infra:
        console.print("\n[bold cyan]Step 4: Ensuring databases…[/bold cyan]")
        for db_name in ("llm_port_backend", "llm_api", "rag", "pii", "langfuse"):
            _ensure_database(db_name)

    # ── 5. Install dependencies ───────────────────────────────────
    if not skip_deps:
        console.print("\n[bold cyan]Step 5: Installing dependencies…[/bold cyan]")
        backend_dir = workspace_path / "llm_port_backend"
        frontend_dir = workspace_path / "llm_port_frontend"

        if backend_dir.exists():
            _install_backend_deps(backend_dir)
        else:
            warning("Backend directory not found. Skipping.")

        if frontend_dir.exists():
            _install_frontend_deps(frontend_dir)
        else:
            warning("Frontend directory not found. Skipping.")
    else:
        info("Skipping dependency installation (--skip-deps).")

    # ── 6. Migrations ─────────────────────────────────────────────
    if not skip_migrations and not skip_infra:
        console.print("\n[bold cyan]Step 6: Running migrations…[/bold cyan]")
        backend_dir = workspace_path / "llm_port_backend"
        if backend_dir.exists():
            _run_migrations(backend_dir)
        else:
            warning("Backend directory not found. Skipping migrations.")
    else:
        info("Skipping migrations.")

    # ── 7. VS Code workspace ─────────────────────────────────────
    console.print("\n[bold cyan]Step 7: Generating VS Code workspace…[/bold cyan]")
    _generate_vscode_workspace(workspace_path)

    # ── Save config ───────────────────────────────────────────────
    cfg = load_config()
    cfg.install_dir = str(shared_dir)
    cfg.dev = DevConfig(
        workspace_dir=str(workspace_path),
        clone_method=clone_method,
        branch=branch,
        repos=list(REPO_DIR_MAP.values()),
    )
    save_config(cfg)
    success("Configuration saved.")

    # ── Summary ───────────────────────────────────────────────────
    console.print("\n[bold green]✨ Development workspace ready![/bold green]")
    console.print(
        "\n[dim]Next steps:\n"
        "  cd {workspace}\n"
        "  llmport dev up        # start backend + frontend\n"
        "  llmport dev status    # check service status\n"
        "  code llm-port.code-workspace  # open in VS Code[/dim]".format(
            workspace=workspace_path
        )
    )
