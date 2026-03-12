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
from llmport.core.env_gen import default_env_vars, write_env_file
from llmport.core.registry import MODULES_COMPAT as KNOWN_MODULES
from llmport.core.settings import LlmportConfig, load_config, save_config


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
    """Locate the llm_port_shared directory relative to workspace."""
    for candidate in (
        workspace / "llm_port_shared",
        workspace.parent / "llm_port_shared",
    ):
        if candidate.is_dir():
            return candidate
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
@click.option("--force-env", is_flag=True, help="Regenerate .env even if it exists.")
@click.option(
    "--skip-doctor", is_flag=True,
    help="Skip pre-flight system checks.",
)
@click.option(
    "--gpu", is_flag=True,
    help="Enable NVIDIA GPU passthrough (requires NVIDIA Container Toolkit).",
)
@click.option(
    "--yes", "-y", is_flag=True,
    help="Auto-confirm all prompts.",
)
def deploy_cmd(
    install_dir: str | None,
    *,
    modules: str,
    no_build: bool,
    no_cache: bool,
    force_env: bool,
    skip_doctor: bool,
    gpu: bool,
    yes: bool,
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
    cfg = load_config()

    if install_dir:
        workspace = Path(install_dir)
    elif cfg.install_dir:
        workspace = Path(cfg.install_dir)
    else:
        workspace = Path.cwd()

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
        if not docker.installed:
            error("Docker is not installed. Install Docker Engine 24+ first.")
            sys.exit(1)
        if not docker.compose_installed:
            error("Docker Compose V2 is not installed.")
            sys.exit(1)
        if not docker.daemon_running:
            error("Docker daemon is not running. Start Docker first.")
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

    # If --gpu, auto-detect host HF cache and write to .env
    if gpu:
        from llmport.core.env_gen import read_env_file  # noqa: PLC0415

        existing = read_env_file(env_path)
        if "HF_CACHE_DIR" not in existing:
            hf_default = Path.home() / ".cache" / "huggingface" / "hub"
            if hf_default.is_dir():
                with env_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n# HuggingFace cache — auto-detected, mount into backend\n")
                    f.write(f"HF_CACHE_DIR={hf_default}\n")
                info(f"Auto-detected HF cache: {hf_default}")
            else:
                info("No HuggingFace cache found at default path — set HF_CACHE_DIR in .env to mount one")
        # Ensure empty fallback directory exists for compose
        fallback_dir = shared_dir / ".empty-hf-cache"
        fallback_dir.mkdir(exist_ok=True)

    # ── 4. Save config ────────────────────────────────────────────
    cfg.install_dir = str(shared_dir)
    cfg.compose_file = compose_file.name
    cfg.profiles = sorted(profiles)
    save_config(cfg)

    # ── 5. Build images ───────────────────────────────────────────
    compose_files = [compose_file]
    if gpu:
        gpu_overlay = shared_dir / "docker-compose.gpu.yaml"
        if gpu_overlay.exists():
            compose_files.append(gpu_overlay)
            info("GPU passthrough enabled (docker-compose.gpu.yaml)")
        else:
            warning(f"GPU overlay not found at {gpu_overlay}, skipping GPU passthrough.")

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

    rc = compose_up(ctx, detach=True, build=False, pull="missing", wait=True, timeout=180)
    if rc != 0:
        error(f"docker compose up failed (exit code {rc}).")
        sys.exit(rc)
    success("All services started.")

    # ── 7. Initial admin setup ────────────────────────────────────
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
    if wait_for_backend(backend_url):
        creds = bootstrap_interactive(
            backend_url,
            shared_dir,
            auto_confirm=yes,
        )
        if creds:
            cfg.admin_email = creds["email"]
            save_config(cfg)
    else:
        warning("Backend did not become healthy in time — skipping admin setup.")
        console.print("  [dim]Run 'llmport deploy' again or create an admin via the UI.[/dim]")

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
