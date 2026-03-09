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
from llmport.core.detect import detect_docker
from llmport.core.env_gen import dev_env_vars, write_env_file
from llmport.core.git import clone_all_repos
from llmport.core.install import ensure_prerequisites
from llmport.core.registry import (
    BACKEND_DEV_ENV,
    DATABASES,
    MODULES_COMPAT,
    POSTGRES_CONTAINER,
)
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


def _wait_for_postgres(container: str = POSTGRES_CONTAINER, timeout: int = 60) -> bool:
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


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file."""
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _backend_db_creds(backend_dir: Path) -> tuple[str, str, str]:
    """Resolve backend DB user/password/database from backend .env or defaults."""
    defaults = ("llm_port_backend", "llm_port_backend", "llm_port_backend")
    env_values = _parse_env_file(backend_dir / ".env")
    return (
        env_values.get("LLM_PORT_BACKEND_DB_USER", defaults[0]),
        env_values.get("LLM_PORT_BACKEND_DB_PASS", defaults[1]),
        env_values.get("LLM_PORT_BACKEND_DB_BASE", defaults[2]),
    )


def _ensure_backend_role(backend_dir: Path, pg_user: str = "postgres") -> None:
    """Ensure backend DB role exists and can migrate the backend database."""
    db_user, db_pass, db_name = _backend_db_creds(backend_dir)
    role_lit = db_user.replace("'", "''")
    pass_lit = db_pass.replace("'", "''")
    db_lit = db_name.replace("'", "''")
    role_ident = '"' + db_user.replace('"', '""') + '"'

    create_or_update_role_sql = (
        "DO $$ "
        "BEGIN "
        f"  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role_lit}') THEN "
        f"    CREATE ROLE {role_ident} LOGIN PASSWORD '{pass_lit}'; "
        "  ELSE "
        f"    ALTER ROLE {role_ident} WITH LOGIN PASSWORD '{pass_lit}'; "
        "  END IF; "
        "END "
        "$$;"
    )
    role_result = subprocess.run(
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "psql",
            "-U",
            pg_user,
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            "postgres",
            "-c",
            create_or_update_role_sql,
        ],
        capture_output=True,
        text=True,
    )
    if role_result.returncode != 0:
        warning(
            "Could not ensure backend DB role before migrations:\n"
            f"{(role_result.stderr or role_result.stdout).strip()}"
        )
        return

    grant_db_sql = (
        f"GRANT CONNECT ON DATABASE \"{db_lit}\" TO {role_ident}; "
        f"GRANT ALL PRIVILEGES ON DATABASE \"{db_lit}\" TO {role_ident};"
    )
    subprocess.run(
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "psql",
            "-U",
            pg_user,
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            "postgres",
            "-c",
            grant_db_sql,
        ],
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "psql",
            "-U",
            pg_user,
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            db_name,
            "-c",
            f"GRANT ALL ON SCHEMA public TO {role_ident};",
        ],
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "psql",
            "-U",
            pg_user,
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            db_name,
            "-c",
            (
                f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {role_ident}; "
                f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {role_ident}; "
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT ALL PRIVILEGES ON TABLES TO {role_ident}; "
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT ALL PRIVILEGES ON SEQUENCES TO {role_ident};"
            ),
        ],
        capture_output=True,
        text=True,
    )


def _ensure_database(db_name: str, pg_user: str = "postgres") -> None:
    """Ensure a database exists inside the shared Postgres container."""
    check_sql = f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"
    result = subprocess.run(
        ["docker", "exec", POSTGRES_CONTAINER, "psql", "-U", pg_user, "-tAc", check_sql],
        capture_output=True,
        text=True,
    )
    if "1" in (result.stdout or ""):
        info(f"Database '{db_name}' already exists.")
        return

    subprocess.run(
        ["docker", "exec", POSTGRES_CONTAINER, "psql", "-U", pg_user, "-c", f"CREATE DATABASE {db_name};"],
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
    _ensure_backend_role(backend_dir)
    db_user, db_pass, db_base = _backend_db_creds(backend_dir)
    env = os.environ.copy()
    # Ensure backend Alembic connects with the right credentials.
    env.setdefault("LLM_PORT_BACKEND_DB_HOST", "localhost")
    env.setdefault("LLM_PORT_BACKEND_DB_PORT", "5432")
    env.setdefault("LLM_PORT_BACKEND_DB_USER", db_user)
    env.setdefault("LLM_PORT_BACKEND_DB_PASS", db_pass)
    env.setdefault("LLM_PORT_BACKEND_DB_BASE", db_base)
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
@click.option(
    "--modules",
    default=None,
    help="Comma-separated list of modules to enable (e.g. rag,pii,auth).",
)
@click.option("--force-env", is_flag=True, help="Regenerate .env files even if they already exist.")
@click.option("--install-prereqs", is_flag=True, help="Auto-install missing prerequisites (uv, git, node).")
def dev_init(
    workspace: str,
    *,
    ssh: bool,
    branch: str,
    overwrite: bool,
    skip_infra: bool,
    skip_deps: bool,
    skip_migrations: bool,
    modules: str | None,
    force_env: bool,
    install_prereqs: bool,
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

    # Parse --modules into profile list
    profiles: list[str] = []
    if modules:
        for m in modules.split(","):
            m = m.strip().lower()
            if m in MODULES_COMPAT:
                profiles.append(MODULES_COMPAT[m]["profile"])
            else:
                warning(f"Unknown module '{m}'. Available: {', '.join(MODULES_COMPAT)}")

    console.print(f"\n[bold magenta]llm.port Developer Workspace Setup[/bold magenta]")
    console.print(f"[dim]Workspace: {workspace_path}[/dim]")
    if profiles:
        console.print(f"[dim]Modules:   {', '.join(profiles)}[/dim]")
    console.print()

    # ── Prerequisites ─────────────────────────────────────────────
    console.print("[bold cyan]Checking prerequisites…[/bold cyan]")
    all_ok = ensure_prerequisites(install=install_prereqs)
    if not all_ok:
        error(
            "Some prerequisites are missing.\n"
            "  Run: llmport dev doctor --install"
        )
        sys.exit(1)

    # Docker daemon must also be running
    docker_info = detect_docker()
    if not docker_info.daemon_running:
        error("Docker daemon is not running. Start Docker Desktop.")
        sys.exit(1)

    success("Prerequisites OK.")

    # ── 1. Clone repos ────────────────────────────────────────────
    console.print("\n[bold cyan]Step 1: Cloning repositories…[/bold cyan]")
    clone_method = "ssh" if ssh else "https"

    # Resolve GitHub token for cloning private repos (if configured)
    existing_cfg = load_config()
    github_token = existing_cfg.dev.github_token if existing_cfg.dev else ""

    results = clone_all_repos(list(REPO_DIR_MAP.keys()), target_dir=workspace_path, method=clone_method, branch=branch, force=overwrite, token=github_token)

    cloned = sum(1 for r in results if r.cloned)
    skipped = sum(1 for r in results if r.skipped)
    errors = [r for r in results if r.error]
    info(f"Cloned: {cloned}, Skipped (already exist): {skipped}, Errors: {len(errors)}")
    for r in errors:
        error(f"  {r.repo}: {r.error}")

    # ── 2. Generate .env ──────────────────────────────────────────
    console.print("\n[bold cyan]Step 2: Generating .env files…[/bold cyan]")

    # ── 2a. Shared infrastructure .env ────────────────────────────
    shared_dir = workspace_path / "llm_port_shared"
    env_path = shared_dir / ".env"
    if env_path.exists() and not force_env:
        warning(f".env already exists at {env_path} — skipping (use --force-env to regenerate).")
    else:
        env_vars = dev_env_vars(profiles=profiles)
        write_env_file(env_path, env_vars)
        success(f".env written to {env_path}")

    # ── 2b. Backend local .env (so uv run uses localhost) ─────────
    backend_dir = workspace_path / "llm_port_backend"
    backend_env_path = backend_dir / ".env"
    if backend_env_path.exists() and not force_env:
        warning(f"Backend .env already exists at {backend_env_path} — skipping (use --force-env to regenerate).")
    elif backend_dir.exists():
        write_env_file(backend_env_path, dict(BACKEND_DEV_ENV))
        success(f"Backend .env written to {backend_env_path}")

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
        for db_name in DATABASES:
            _ensure_database(db_name)
        if backend_dir.exists():
            _ensure_backend_role(backend_dir)

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
    cfg.profiles = sorted(set(cfg.profiles or []) | set(profiles))
    cfg.dev = DevConfig(
        workspace_dir=str(workspace_path),
        clone_method=clone_method,
        branch=branch,
        repos=list(REPO_DIR_MAP.keys()),
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
