"""``llmport upgrade`` — upgrade the running llm.port installation.

Performs a safe in-place upgrade:
  1. Pre-flight checks (Docker, disk)
  2. Auto-backup (unless --no-backup)
  3. Refresh the deployment files and .env (existing values kept)
  4. Pull this release's images (an install made by ``llmport deploy``
     from the published images), or rebuild them (a source checkout)
  5. Rolling restart: infra → migrators → app → modules
  6. Health gate: poll backend /api/health
  7. Print summary

Examples:
    llmport upgrade                  # standard upgrade
    llmport upgrade --no-backup      # skip pre-upgrade backup
    llmport upgrade --no-build       # skip image rebuild
    llmport upgrade --dry-run        # show what would happen
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from llmport import __version__
from llmport.core.backup import create_backup
from llmport.core.bundle import installed_release, unpack
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
from llmport.core.settings import load_config, save_config


@click.command("upgrade")
@click.option("--no-backup", is_flag=True, default=False, help="Skip pre-upgrade backup.")
@click.option(
    "--backup-dir",
    type=click.Path(file_okay=False),
    default="backups",
    show_default=True,
    help="Directory for the pre-upgrade backup.",
)
@click.option("--no-build", is_flag=True, default=False, help="Skip image rebuild.")
@click.option("--no-cache", is_flag=True, default=False, help="Build images without cache.")
@click.option("--skip-doctor", is_flag=True, default=False, help="Skip pre-flight checks.")
@click.option(
    "--runtime-images",
    "runtime_images",
    default=None,
    metavar="ARCHS",
    help=(
        "Runtime images to fetch for machines: all, none, or architectures such as "
        "x86_64,aarch64. Default: what deploy chose (all)."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would happen without executing.",
)
@click.option("-y", "--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
def upgrade_cmd(
    *,
    no_backup: bool,
    backup_dir: str,
    no_build: bool,
    no_cache: bool,
    skip_doctor: bool,
    runtime_images: str | None,
    dry_run: bool,
    yes: bool,
) -> None:
    """Upgrade the running llm.port installation in place.

    Creates a backup, refreshes configuration (preserving secrets),
    rebuilds container images, and performs a rolling restart with
    a health gate. Then fetches the runtime images this release pins, which
    machines download from this server.
    """
    console.print("\n[bold magenta]llm.port — Upgrade[/bold magenta]\n")

    cfg = load_config()

    if not cfg.install_dir:
        error(
            "No install directory configured.\n"
            "  Run 'llmport deploy' first, or set install_dir in llmport.yaml."
        )
        sys.exit(1)

    shared_dir = cfg.install_path
    compose_file = shared_dir / cfg.compose_file
    if not compose_file.exists():
        error(f"Compose file not found: {compose_file}")
        sys.exit(1)

    env_path = shared_dir / ".env"

    # An install made from the published images moves to this CLI's release;
    # a source checkout rebuilds from its own code.
    release = installed_release(shared_dir)
    if release is not None and _older(__version__, release):
        error(
            f"This CLI ({__version__}) is older than the install ({release}).\n"
            "  Its database has already been migrated past this release; going back is a restore:\n"
            "    llmport restore <install_dir>/backups/<time>\n"
            "  To upgrade, install the newer CLI first: pip install -U llmport-cli"
        )
        sys.exit(1)

    # Build compose file list (include GPU overlay if present)
    from llmport.core.compose import has_nvidia_gpu  # noqa: PLC0415

    compose_files: list[Path] = [compose_file]
    gpu_overlay = shared_dir / "docker-compose.gpu.yaml"
    if has_nvidia_gpu() and gpu_overlay.exists():
        compose_files.append(gpu_overlay)

    ctx = ComposeContext(
        compose_files=compose_files,
        env_file=env_path if env_path.exists() else None,
        project_dir=shared_dir,
        profiles=list(cfg.profiles),
    )

    # ── Dry-run summary ───────────────────────────────────────
    if dry_run:
        console.print("[bold]Dry-run — no changes will be made.[/bold]\n")
        console.print(f"  Install directory: {shared_dir}")
        console.print(f"  Compose file:      {compose_file.name}")
        console.print(f"  Profiles:          {', '.join(cfg.profiles) or '(none)'}")
        console.print(f"  Backup:            {'skip' if no_backup else backup_dir}")
        if release is not None:
            console.print(f"  Release:           {release} -> {__version__} (pull published images)")
        else:
            console.print(f"  Build images:      {'skip' if no_build else 'yes'}")
        console.print(f"  Build cache:       {'no' if no_cache else 'yes'}")
        console.print(f"  Runtime images:    {runtime_images or cfg.runtime_images} (for machines)")
        return

    # ── Confirm ───────────────────────────────────────────────
    if not yes:
        click.confirm("Proceed with upgrade?", default=True, abort=True)

    step = 0

    # ── 1. Pre-flight checks ─────────────────────────────────
    if not skip_doctor:
        step += 1
        console.print(f"\n[bold cyan]Step {step}: Pre-flight checks…[/bold cyan]")

        docker = detect_docker()
        if not docker.ok:
            error("Docker is not available or the daemon is not running.")
            if docker.error:
                error(f"  {docker.error}")
            sys.exit(1)
        success(
            f"Docker {docker.version}, "
            f"Compose {docker.compose_version}, "
            f"daemon running"
        )
    else:
        console.print("\n[dim]Skipping pre-flight checks (--skip-doctor).[/dim]")

    # Before the backup and the builds, not after: compose cannot replace a
    # container it did not create, and the restart would stop on it.
    from llmport.core.compose import foreign_containers  # noqa: PLC0415

    foreign = foreign_containers(ctx)
    if foreign:
        error(
            "These containers have names this installation uses, but it did not create them,\n"
            "  so it cannot replace them:\n"
            + "".join(
                f"    {name}  (made by {f'compose project {owner}' if owner else 'docker run'})\n"
                for name, owner in foreign
            )
            + "  Remove them -- their data volumes stay -- and run the upgrade again:\n"
            f"    docker rm -f {' '.join(name for name, _ in foreign)}"
        )
        sys.exit(1)

    # ── 2. Pre-upgrade backup ─────────────────────────────────
    if not no_backup:
        step += 1
        console.print(f"\n[bold cyan]Step {step}: Pre-upgrade backup…[/bold cyan]")

        backup_path = Path(backup_dir)
        if not backup_path.is_absolute():
            backup_path = shared_dir / backup_path

        result = create_backup(ctx, output_dir=backup_path)
        if not result.ok:
            for msg in result.errors:
                error(msg)
            error("Backup failed — aborting upgrade. Fix the issue or use --no-backup.")
            sys.exit(1)
        success(f"Backup created: {result.backup_dir}")
    else:
        console.print("\n[dim]Skipping backup (--no-backup).[/dim]")

    # ── 3. Refresh .env (preserve secrets) ────────────────────
    step += 1
    console.print(f"\n[bold cyan]Step {step}: Refreshing environment…[/bold cyan]")

    if release is not None:
        changed = unpack(shared_dir)
        success(f"Deployment files for release {__version__} ({len(changed)} changed).")

    if env_path.exists():
        # What the install has wins; a new release only adds what it
        # introduced. Rewritten from the defaults with just the secrets kept,
        # every upgrade dropped what deploy and the operator had set --
        # HF_CACHE_DIR (the host's model cache), the admin name synced to
        # Grafana, ports -- and the services came back without them.
        env_vars = default_env_vars(profiles=list(cfg.profiles))
        env_vars.update(read_env_file(env_path))
        write_env_file(env_path, env_vars)
        if release is not None:
            from llmport.commands.deploy import pin_release  # noqa: PLC0415

            pin_release(env_path)
        success(".env refreshed (existing settings kept).")
        # RabbitMQ makes its users from definitions.json on every start; the
        # services log in with the passwords in .env. Written only by deploy,
        # the file kept an older install's passwords, and after an upgrade
        # the gateway and backend were refused ("invalid credentials").
        from llmport.commands.deploy import _regenerate_rmq_definitions  # noqa: PLC0415

        _regenerate_rmq_definitions(shared_dir, read_env_file(env_path), cfg.profiles)
        success("RabbitMQ users written from .env.")
    else:
        warning("No .env file found — skipping env refresh.")

    # ── 4. Images ─────────────────────────────────────────────
    if release is not None:
        step += 1
        console.print(f"\n[bold cyan]Step {step}: Pulling release {__version__}…[/bold cyan]")
        from llmport.core.compose import pull as compose_pull  # noqa: PLC0415

        rc = compose_pull(ctx)
        if rc != 0:
            error("Could not pull this release's images; nothing was restarted.")
            sys.exit(rc)
        success("Images pulled.")
    elif not no_build:
        step += 1
        console.print(f"\n[bold cyan]Step {step}: Building container images…[/bold cyan]")
        console.print("[dim]This may take several minutes.[/dim]")

        pull_base_images()

        info("Building platform base image…")
        rc = build_base_image(ctx, no_cache=no_cache)
        if rc != 0:
            warning("Base image build failed — service builds may fail.")
        else:
            success("Base image built.")

        rc = compose_build(ctx, no_cache=no_cache)
        if rc != 0:
            warning("Some images failed to build. Continuing with available images.")
        else:
            success("All images built successfully.")
    else:
        console.print("\n[dim]Skipping image build (--no-build).[/dim]")

    # ── 5. Rolling restart ────────────────────────────────────
    step += 1
    console.print(f"\n[bold cyan]Step {step}: Rolling restart…[/bold cyan]")

    # Sync postgres password before restarting (same as deploy)
    from llmport.commands.deploy import _sync_postgres_password  # noqa: PLC0415

    _sync_postgres_password(ctx, env_path)

    # Bring everything up (force-recreate to pick up new images)
    rc = compose_up(ctx, detach=True, build=False, pull="missing", force_recreate=True, wait=True, timeout=180)
    if rc != 0:
        error(f"docker compose up failed (exit code {rc}).")
        sys.exit(rc)
    success("All services restarted.")

    # ClickHouse's diagnostic logs are off from this release; what they hold
    # stays until dropped (41 GB of trace_log filled one install's disk).
    from llmport.core.clickhouse import drop_stale_logs  # noqa: PLC0415

    try:
        dropped = drop_stale_logs(ctx)
    except RuntimeError as exc:
        warning(f"Could not clear ClickHouse's old diagnostic logs: {exc}")
    else:
        if dropped:
            success(f"Dropped ClickHouse's old diagnostic logs: {', '.join(dropped)}.")

    # ── 6. Health gate ────────────────────────────────────────
    step += 1
    console.print(f"\n[bold cyan]Step {step}: Health check…[/bold cyan]")

    from llmport.core.bootstrap import wait_for_backend  # noqa: PLC0415

    http_port = 80
    if env_path.exists():
        existing = read_env_file(env_path)
        port_str = existing.get("LLM_PORT_HTTP_PORT", "80")
        try:
            http_port = int(port_str)
        except ValueError:
            pass
    backend_url = f"http://localhost:{http_port}" if http_port != 80 else "http://localhost"

    console.print("  [dim]Waiting for backend to become healthy…[/dim]")
    if wait_for_backend(backend_url, timeout=120):
        success("Backend is healthy.")
    else:
        warning(
            "Backend did not become healthy within 120 s.\n"
            "  Check logs: llmport logs backend"
        )

    # ── 7. Runtime images ─────────────────────────────────────
    # After the restart, not before it: the service images are what the
    # upgrade waits on, and a runtime image is several gigabytes that only
    # matters once a machine starts a cluster.
    step += 1
    console.print(f"\n[bold cyan]Step {step}: Runtime images for machines…[/bold cyan]")
    from llmport.commands.runtime_images import run_step  # noqa: PLC0415

    run_step(cfg, value=runtime_images)

    # ── Done ──────────────────────────────────────────────────
    console.print()
    success("Upgrade complete.")


def _older(a: str, b: str) -> bool:
    """Whether release *a* comes before release *b* (``0.3.0`` < ``0.10.0``)."""

    def parts(v: str) -> tuple[int, ...]:
        out = []
        for piece in v.split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return tuple(out)

    return parts(a) < parts(b)
