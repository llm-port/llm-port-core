"""``llmport deploy`` — full production deployment in one command.

Runs the complete production flow:
  1. Pre-flight checks (Docker, Compose, disk, RAM)
  2. Locate or set up the install directory
  3. Generate ``.env`` with random secrets and auto-tuned pool sizes
  4. Enable requested modules (compose profiles)
  5. Build all container images from source
  6. Start all services (infra + app + modules)
  7. Print endpoint summary
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from llmport.core.compose import (
    ComposeContext,
    build as compose_build,
    build_base_image,
    build_context_from_config,
    pull_base_images,
    up as compose_up,
)
from llmport.core.console import console, error, info, success, warning
from llmport.core.detect import detect_docker
from llmport.core.env_gen import default_env_vars, read_env_file, write_env_file
from llmport.core.registry import MODULES_COMPAT as KNOWN_MODULES
from llmport.core.rmq import write_definitions as write_rmq_definitions
from llmport.core.settings import LlmportConfig, load_config, save_config


def _regenerate_rmq_definitions(
    shared_dir: Path,
    env_vars: dict[str, str],
    profiles: set[str] | list[str],
) -> None:
    """Write ``rabbitmq/definitions.json`` from current env vars."""
    write_rmq_definitions(
        shared_dir,
        admin_user=env_vars.get("RABBITMQ_ADMIN_USER", "admin"),
        admin_pass=env_vars.get("RABBITMQ_ADMIN_PASS", "guest"),
        backend_pass=env_vars.get("RABBITMQ_BACKEND_PASS", "guest"),
        api_pass=env_vars.get("RABBITMQ_API_PASS", "guest"),
        pii_pass=env_vars.get("RABBITMQ_PII_PASS") if "pii" in set(profiles) else None,
    )


def _sync_postgres_password(ctx: ComposeContext, env_path: Path) -> None:
    """Ensure the Postgres superuser password matches ``.env``.

    The official ``postgres`` image only reads ``POSTGRES_PASSWORD``
    during the **first** initialisation of the data volume.  If the
    volume already exists and ``.env`` contains a different password,
    every service that connects with the new password will fail with
    ``InvalidPasswordError``.

    This function boots only the ``postgres`` service, waits for it to
    become healthy, then runs ``ALTER USER`` via local socket auth
    (which never requires a password) to bring the DB in sync.
    """
    import shutil
    import subprocess

    from llmport.core.env_gen import read_env_file

    docker = shutil.which("docker")
    if not docker:
        return

    env_vars = read_env_file(env_path)
    pg_user = env_vars.get("POSTGRES_USER", "postgres")
    pg_pass = env_vars.get("POSTGRES_PASSWORD", "")
    if not pg_pass:
        return

    info("Syncing Postgres password with .env…")

    # Start only postgres so we can ALTER before migrators run.
    compose_up(ctx, services=["postgres"], detach=True, wait=True, timeout=60)

    # ALTER via local socket auth (no password needed inside container).
    # Use stdin pipe to avoid shell-quoting issues with special chars.
    # Escape single quotes in password for SQL literal safety.
    safe_pass = pg_pass.replace("'", "''")
    alter_sql = f"ALTER USER {pg_user} PASSWORD '{safe_pass}';\n"
    result = subprocess.run(  # noqa: S603
        [docker, "exec", "-i", "llm-port-postgres", "psql", "-U", pg_user],
        input=alter_sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        success("Postgres password synchronised.")
    else:
        warning(f"Could not sync Postgres password: {result.stderr.strip()}")


def _resolve_compose_file(install_dir: Path) -> Path | None:
    """Find the shared docker-compose file."""
    for candidate in (
        install_dir / "docker-compose.yaml",
        install_dir / "docker-compose.yml",
    ):
        if candidate.exists():
            return candidate
    return None


def _find_shared_dir(workspace: Path) -> Path | None:
    """Locate the llm_port_shared directory relative to workspace.

    Searches in order:
      1. workspace/llm_port_shared        (monorepo layout)
      2. workspace/llm-port-core/llm_port_shared  (monorepo cloned)
      3. workspace/../llm_port_shared      (legacy polyrepo sibling)
    """
    for candidate in (
        workspace / "llm_port_shared",
        workspace / "llm-port-core" / "llm_port_shared",
        workspace.parent / "llm_port_shared",
    ):
        if candidate.is_dir():
            return candidate
    return None


def _login_for_api_token(backend_url: str, email: str) -> str | None:
    """Prompt for password, login via the auth endpoint, and return an access token."""
    import click  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from llmport.core.console import error, success  # noqa: PLC0415

    password = click.prompt(f"  Password for {email}", hide_input=True, show_default=False)
    try:
        resp = httpx.post(
            f"{backend_url.rstrip('/')}/api/auth/jwt/login",
            data={"username": email, "password": password},
            timeout=15,
        )
        if resp.status_code == 200:  # noqa: PLR2004
            token = resp.json().get("access_token", "")
            if token:
                success("Logged in — creating enrollment token…")
                return token
        error(f"Login failed (HTTP {resp.status_code})")
        return None
    except httpx.HTTPError as exc:
        error(f"Could not reach backend for login: {exc}")
        return None


@click.command("deploy")
@click.argument(
    "install_dir",
    required=False,
    type=click.Path(file_okay=False, resolve_path=True),
)
@click.option(
    "--modules", "-m",
    default="",
    help="Comma-separated modules to enable (EE only, e.g. pii,auth).",
)
@click.option("--no-build", is_flag=True, help="Skip building images (pull only).")
@click.option("--no-cache", is_flag=True, help="Build images without Docker cache.")
@click.option("--gpu/--no-gpu", default=None, help="Include GPU (NVIDIA) compose overlay. Default: auto-detect.")
@click.option("--force-env", is_flag=True, help="Regenerate .env even if it exists.")
@click.option(
    "--skip-doctor", is_flag=True,
    help="Skip pre-flight system checks.",
)
@click.option(
    "--yes", "-y", is_flag=True,
    help="Auto-confirm all prompts.",
)
@click.option(
    "--local-node",
    is_flag=True,
    help="Provision llm_port_node_agent locally or over SSH during deployment.",
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
    default="",
    help="Backend URL written to node-agent environment. Default: same as deploy target.",
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
def deploy_cmd(
    install_dir: str | None,
    *,
    modules: str,
    no_build: bool,
    no_cache: bool,
    gpu: bool | None,
    force_env: bool,
    skip_doctor: bool,
    yes: bool,
    local_node: bool,
    local_node_host: str,
    local_node_workdir: str,
    local_node_branch: str,
    local_node_backend_url: str,
    local_node_advertise_host: str,
    local_node_enrollment_token: str,
    local_node_sudo: bool,
) -> None:
    """Deploy llm.port to a local production environment.

    Performs a complete deployment: pre-flight checks, environment
    generation, image builds, database migrations, and service startup.

    INSTALL_DIR defaults to the current directory or the configured
    install path.

    \b
    Examples:
        llmport deploy                         # deploy from current workspace
        llmport deploy /opt/llm-port           # deploy to specific directory
        llmport deploy --no-build              # skip building (use existing images)
        llmport deploy --force-env             # regenerate secrets
    """
    console.print("\n[bold magenta]llm.port — Production Deployment[/bold magenta]\n")

    # ── 0. Resolve install directory ──────────────────────────────
    #
    # Priority order:
    #   1. Explicit CLI argument  (llmport deploy /path)
    #   2. Current working directory — if it contains llm_port_shared
    #   3. Saved config install_dir  (from a previous dev init / deploy)
    #   4. CWD as final fallback
    #
    # This ensures that running ``llmport deploy`` from a development
    # workspace always builds from the local source, even when the
    # config still points at a different (possibly stale) clone.
    cfg = load_config()

    if install_dir:
        workspace = Path(install_dir)
    else:
        cwd = Path.cwd()
        cwd_shared = _find_shared_dir(cwd)
        if cwd_shared:
            workspace = cwd
        elif cfg.install_dir:
            workspace = Path(cfg.install_dir)
        else:
            workspace = cwd

    shared_dir = _find_shared_dir(workspace)
    if not shared_dir:
        # If install_dir itself IS the shared dir
        if (workspace / "docker-compose.yaml").exists() or (workspace / "docker-compose.yml").exists():
            shared_dir = workspace
        else:
            error(
                f"Cannot find llm_port_shared in {workspace}.\n"
                "  Run from the workspace root, or pass the install directory as an argument.\n"
                "  If you haven't cloned the repos yet, run: llmport dev init <dir>"
            )
            sys.exit(1)

    compose_file = _resolve_compose_file(shared_dir)
    if not compose_file:
        error(f"No docker-compose.yaml found in {shared_dir}.")
        sys.exit(1)

    info(f"Install directory: {shared_dir}")

    # ── 1. Pre-flight checks ─────────────────────────────────────
    if not skip_doctor:
        console.print("\n[bold cyan]Step 1: Pre-flight checks…[/bold cyan]")

        docker = detect_docker()
        preflight_errors: list[str] = []

        if not docker.installed:
            msg = docker.error or "Docker Engine 24+ is required."
            preflight_errors.append(
                f"Docker is not available. {msg}\n"
                f"  Install: {docker.install_hint}"
            )
        if docker.installed and not docker.compose_installed:
            preflight_errors.append(
                "Docker Compose V2 is not installed.\n"
                f"  Install: {docker.install_hint}"
            )
        if docker.installed and not docker.daemon_running:
            hint = docker.error or docker.daemon_hint
            preflight_errors.append(f"Docker daemon is not running. {hint}")

        if preflight_errors:
            for msg in preflight_errors:
                error(msg)
            sys.exit(1)

        success(
            f"Docker {docker.version}, "
            f"Compose {docker.compose_version}, "
            f"daemon running"
        )
    else:
        console.print("\n[dim]Skipping pre-flight checks (--skip-doctor).[/dim]")

    # ── 2. Parse requested modules ────────────────────────────────
    console.print("\n[bold cyan]Step 2: Module configuration…[/bold cyan]")

    known_profiles = {m["profile"] for m in KNOWN_MODULES.values()}
    profiles: set[str] = {p for p in (cfg.profiles or []) if p in known_profiles}

    if modules:
        for mod in modules.split(","):
            mod = mod.strip().lower()
            if not mod:
                continue
            if mod not in KNOWN_MODULES:
                warning(f"Unknown module: {mod}. Available: {', '.join(sorted(KNOWN_MODULES))}")
                continue
            profiles.add(KNOWN_MODULES[mod]["profile"])
            info(f"Enabled module: {mod}")

    if profiles:
        console.print(f"  Active profiles: [bold]{', '.join(sorted(profiles))}[/bold]")
    else:
        console.print("  No optional modules enabled.")

    # ── 3. Generate .env file ─────────────────────────────────────
    console.print("\n[bold cyan]Step 3: Environment configuration…[/bold cyan]")

    env_path = shared_dir / ".env"
    if env_path.exists() and not force_env:
        info(f".env already exists at {env_path} (use --force-env to regenerate)")
    else:
        env_vars = default_env_vars(profiles=sorted(profiles))
        write_env_file(env_path, env_vars, preserve_secrets=not force_env)
        success(f"Environment file written to {env_path}")

    # Ensure host HuggingFace cache directory exists and is configured
    existing = read_env_file(env_path)
    if "HF_CACHE_DIR" not in existing:
        hf_default = Path.home() / ".cache" / "huggingface" / "hub"
        hf_default.mkdir(parents=True, exist_ok=True)
        # Docker Desktop on Windows needs POSIX-style paths
        # (e.g. /c/Users/…) because ':' is used as the volume
        # mount delimiter in compose files.
        hf_str = str(hf_default)
        if sys.platform == "win32":
            hf_str = hf_default.as_posix()
            # C:/Users/… → /c/Users/…
            if len(hf_str) >= 2 and hf_str[1] == ":":
                hf_str = "/" + hf_str[0].lower() + hf_str[2:]
        with env_path.open("a", encoding="utf-8") as f:
            f.write("\n# HuggingFace cache — mount into backend\n")
            f.write(f"HF_CACHE_DIR={hf_str}\n")
        info(f"HF cache directory: {hf_default}")
    # Ensure empty fallback directory exists for compose
    fallback_dir = shared_dir / ".empty-hf-cache"
    fallback_dir.mkdir(exist_ok=True)

    # ── 3b. Migrate old single-credential RabbitMQ env vars ───────
    existing = read_env_file(env_path)
    if "RABBITMQ_BACKEND_PASS" not in existing:
        from llmport.core.env_gen import _random_password  # noqa: PLC0415

        info("Migrating RabbitMQ credentials to per-service format…")
        existing["RABBITMQ_ADMIN_USER"] = existing.pop("RABBITMQ_USER", "admin")
        existing["RABBITMQ_ADMIN_PASS"] = existing.pop("RABBITMQ_PASS", _random_password(24))
        existing["RABBITMQ_BACKEND_PASS"] = _random_password(24)
        existing["RABBITMQ_API_PASS"] = _random_password(24)
        existing["RABBITMQ_PII_PASS"] = _random_password(24)
        write_env_file(env_path, existing)
        success("RabbitMQ credentials migrated to per-service format.")

    # ── 3c. Generate RabbitMQ definitions.json ─────────────────────
    existing = read_env_file(env_path)
    _regenerate_rmq_definitions(shared_dir, existing, profiles)
    success("RabbitMQ definitions.json generated.")

    # ── 4. Save config ────────────────────────────────────────────
    cfg.install_dir = str(shared_dir)
    cfg.compose_file = compose_file.name
    cfg.profiles = sorted(profiles)
    save_config(cfg)

    # ── 5. Build images ───────────────────────────────────────────
    from llmport.core.compose import has_nvidia_gpu  # noqa: PLC0415

    use_gpu = has_nvidia_gpu() if gpu is None else gpu
    compose_files = [compose_file]
    gpu_overlay = shared_dir / "docker-compose.gpu.yaml"
    if use_gpu and gpu_overlay.exists():
        compose_files.append(gpu_overlay)
        info("NVIDIA GPU detected — including GPU overlay.")
    elif gpu_overlay.exists():
        info("GPU overlay skipped (no NVIDIA runtime detected or --no-gpu).")

    ctx = ComposeContext(
        compose_files=compose_files,
        env_file=env_path if env_path.exists() else None,
        project_dir=shared_dir,
        profiles=sorted(profiles),
    )

    if not no_build:
        console.print("\n[bold cyan]Step 4: Building container images…[/bold cyan]")
        console.print("[dim]This may take several minutes on first run.[/dim]")

        pull_base_images()

        # Build the shared platform base image first (same BuildKit context)
        info("Building platform base image…")
        rc = build_base_image(ctx, no_cache=no_cache)
        if rc != 0:
            warning("Base image build failed — service builds may fail.")
        else:
            success("Base image built.")

        rc = compose_build(ctx, no_cache=no_cache)
        if rc != 0:
            warning(
                "Some images failed to build. Services with successful"
                " builds will still start."
            )
        else:
            success("All images built successfully.")
    else:
        console.print("\n[dim]Skipping image build (--no-build).[/dim]")

    # ── 6. Start services ─────────────────────────────────────────
    console.print("\n[bold cyan]Step 5: Starting services…[/bold cyan]")

    # Start Postgres first and sync its password.
    # The POSTGRES_PASSWORD env var only takes effect on first volume
    # initialisation.  If the data volume already exists with a
    # different password, migrators will fail with InvalidPasswordError.
    # We boot Postgres alone, ALTER USER to match .env, then start
    # everything else.
    _sync_postgres_password(ctx, env_path)

    # Start infrastructure services and wait for them to become healthy
    # BEFORE starting application containers.  This avoids a race
    # condition where workers start before Docker's embedded DNS has
    # registered the RMQ container, causing permanent AMQP connection
    # failures.
    infra_services = ["redis", "llm-port-rmq", "minio"]
    info("Starting infrastructure services (Redis, RabbitMQ, MinIO)…")
    rc = compose_up(ctx, services=infra_services, detach=True, wait=True, timeout=60)
    if rc != 0:
        error(f"Infrastructure services failed to start (exit code {rc}).")
        sys.exit(rc)
    success("Infrastructure services healthy.")

    rc = compose_up(ctx, detach=True, build=False, pull="missing", wait=True, timeout=180)
    if rc != 0:
        error(f"docker compose up failed (exit code {rc}).")
        sys.exit(rc)
    success("All services started.")

    # ── 6. Initial admin setup ────────────────────────────────────
    console.print("\n[bold cyan]Step 6: Initial admin setup…[/bold cyan]")

    from llmport.core.bootstrap import bootstrap_interactive, wait_for_backend  # noqa: PLC0415

    http_port = 80
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("LLM_PORT_HTTP_PORT="):
                    http_port = int(line.split("=", 1)[1].strip().strip('"').strip("'"))
                    break
        except Exception:  # noqa: BLE001
            pass
    backend_url = f"http://localhost:{http_port}" if http_port != 80 else "http://localhost"
    console.print("  [dim]Waiting for backend to become healthy…[/dim]")
    creds = None
    if wait_for_backend(backend_url):
        creds = bootstrap_interactive(
            backend_url,
            shared_dir,
            auto_confirm=yes,
        )
        if creds:
            cfg.admin_email = creds["email"]
            save_config(cfg)

            # ── Sync credentials to Grafana, Langfuse & RabbitMQ ──
            from llmport.core.compose import up as compose_up_svc  # noqa: PLC0415

            env_vars = read_env_file(env_path)
            env_vars["GRAFANA_ADMIN_USER"] = creds["email"]
            env_vars["GRAFANA_ADMIN_PASSWORD"] = creds["password"]
            # Only the management/admin password is synced to the admin
            # credential.  Per-service AMQP passwords (RABBITMQ_BACKEND_PASS,
            # etc.) are never overwritten — they are stable random secrets
            # set during env generation.
            env_vars["RABBITMQ_ADMIN_PASS"] = creds["password"]
            env_vars["LANGFUSE_INIT_USER_EMAIL"] = creds["email"]
            env_vars["LANGFUSE_INIT_USER_PASSWORD"] = creds["password"]
            env_vars["LANGFUSE_INIT_USER_NAME"] = "Admin"
            env_vars["LANGFUSE_INIT_PROJECT_NAME"] = "llm-port"
            if creds.get("api_token"):
                env_vars["LANGFUSE_INIT_PROJECT_SECRET_KEY"] = creds["api_token"][:40]
                env_vars["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"] = "pk-lf-llmport"

            write_env_file(env_path, env_vars)
            info("Synced admin credentials to Grafana, Langfuse & RabbitMQ.")

            # Regenerate definitions.json with the new admin password.
            _regenerate_rmq_definitions(shared_dir, env_vars, profiles)

            # Force-recreate so containers pick up the new env vars.
            # RabbitMQ loads definitions.json on startup which now
            # includes the admin password.  Service workers are NOT
            # recreated because their per-service passwords have not
            # changed.
            recreate_services = ["grafana", "langfuse-web", "llm-port-rmq"]
            compose_up_svc(
                ctx,
                services=recreate_services,
                detach=True,
                force_recreate=True,
                wait=True,
                timeout=60,
            )
            success("Grafana, Langfuse & RabbitMQ recreated with shared credentials.")
    else:
        warning("Backend did not become healthy in time — skipping admin setup.")
        console.print("  [dim]Run 'llmport deploy' again or create an admin via the UI.[/dim]")

    # ── 7. Optional local-node provisioning ───────────────────────
    if local_node:
        console.print("\n[bold cyan]Step 7: Local node-agent provisioning…[/bold cyan]")
        from llmport.core.local_node import (  # noqa: PLC0415
            create_enrollment_token,
            provision_local_node_agent,
        )

        # Auto-create enrollment token if none was provided and
        # bootstrap gave us an API token.
        enrollment_token = local_node_enrollment_token
        if not enrollment_token.strip() and creds and creds.get("api_token"):
            info("No enrollment token provided — creating one automatically…")
            enrollment_token = create_enrollment_token(backend_url, creds["api_token"]) or ""
        if not enrollment_token.strip():
            # Bootstrap already happened — try logging in to get a token.
            info("No API token from bootstrap — attempting login…")
            api_token = _login_for_api_token(backend_url, cfg.admin_email)
            if api_token:
                enrollment_token = create_enrollment_token(backend_url, api_token) or ""
        if not enrollment_token.strip():
            warning(
                "No enrollment token available. Provide one with"
                " --local-node-enrollment-token or re-run after bootstrap."
            )

        branch = local_node_branch.strip() or (cfg.dev.branch if cfg.dev and cfg.dev.branch else "master")
        method = cfg.dev.clone_method if cfg.dev and cfg.dev.clone_method else "https"
        github_token = cfg.dev.github_token if cfg.dev else ""
        remote_host = local_node_host.strip() or None
        workspace_for_node = shared_dir.parent

        # Default to the resolved backend URL (via nginx) if not explicitly set.
        node_backend = local_node_backend_url.strip() or backend_url

        ok = provision_local_node_agent(
            workspace=workspace_for_node,
            branch=branch,
            backend_url=node_backend,
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

    # ── 8. Endpoint summary ───────────────────────────────────────
    console.print("\n[bold green]✨ Deployment complete![/bold green]\n")

    from rich.table import Table

    table = Table(title="Service Endpoints", show_header=True, header_style="bold cyan")
    table.add_column("Service", style="bold")
    table.add_column("URL")

    base = f"http://localhost:{http_port}" if http_port != 80 else "http://localhost"
    endpoints = [
        ("Application", base),
        ("API Docs", f"{base}/api/docs"),
        ("OpenAI-compat Gateway", f"{base}/v1/"),
        ("Grafana", "http://localhost:3001 (local only)"),
        ("Langfuse", "http://localhost:3002 (local only)"),
        ("RabbitMQ Management", "http://localhost:15672 (local only)"),
    ]

    for name, url in endpoints:
        table.add_row(name, url)

    console.print(table)

    console.print(
        "\n[dim]Useful commands:\n"
        "  llmport status        — container status\n"
        "  llmport logs -f       — follow logs\n"
        "  llmport module list   — show enabled modules\n"
        "  llmport down          — stop all services\n"
        "  llmport deploy --help — deployment options[/dim]"
    )
