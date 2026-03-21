"""``llmport restore`` — restore from a backup directory.

Examples:
    llmport restore backups/20250115T120000Z
    llmport restore backups/20250115T120000Z --verify-only
    llmport restore backups/20250115T120000Z --db-only
    llmport restore backups/20250115T120000Z --skip-env
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from llmport.core.backup import (
    read_manifest,
    restore_backup,
    verify_backup,
)
from llmport.core.compose import (
    build_context_from_config,
    down as compose_down,
    up as compose_up,
)
from llmport.core.console import console, error, info, success, warning
from llmport.core.settings import load_config
from llmport.commands.deploy import _sync_postgres_password


@click.command("restore")
@click.argument("backup_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--db-only", is_flag=True, default=False, help="Restore databases only.")
@click.option("--skip-env", is_flag=True, default=False, help="Don't overwrite the current .env.")
@click.option(
    "--verify-only",
    is_flag=True,
    default=False,
    help="Validate checksums without restoring.",
)
@click.option("-y", "--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
def restore_cmd(
    backup_dir: str,
    *,
    db_only: bool,
    skip_env: bool,
    verify_only: bool,
    yes: bool,
) -> None:
    """Restore llm.port from a backup directory.

    BACKUP_DIR is the path to a timestamped backup folder containing
    a manifest.json file created by ``llmport backup``.
    """
    console.print("\n[bold magenta]llm.port — Restore[/bold magenta]\n")

    backup_path = Path(backup_dir).resolve()
    manifest = read_manifest(backup_path)
    if manifest is None:
        error(f"No manifest.json found in {backup_path}")
        sys.exit(1)

    # ── Show backup info ──────────────────────────────────────
    console.print(f"  Backup date:    {manifest.get('created_at', 'unknown')}")
    console.print(f"  llmport version: {manifest.get('llmport_version', 'unknown')}")
    db_names = list(manifest.get("databases", {}).keys())
    console.print(f"  Databases:      {', '.join(db_names)}")
    if manifest.get("env_snapshot"):
        console.print(f"  .env snapshot:  {manifest['env_snapshot']}")
    if manifest.get("volumes"):
        console.print(f"  Volumes:        {', '.join(manifest['volumes'])}")
    console.print()

    # ── Verify checksums ──────────────────────────────────────
    info("Verifying backup checksums…")
    check_errors = verify_backup(backup_path, manifest)
    if check_errors:
        for msg in check_errors:
            error(f"  {msg}")
        sys.exit(1)
    success("Checksums OK.")

    if verify_only:
        return

    # ── Confirm ───────────────────────────────────────────────
    if not yes:
        console.print()
        warning("This will overwrite current databases with the backup data.")
        click.confirm("Proceed with restore?", default=False, abort=True)

    cfg = load_config()
    ctx = build_context_from_config(cfg)

    # ── Stop application services (keep postgres) ─────────────
    console.print("\n[bold cyan]Stopping application services…[/bold cyan]")
    compose_down(ctx, remove_orphans=False)
    # Bring postgres back up for the restore
    compose_up(ctx, services=["postgres"], detach=True, wait=True, timeout=60)

    # ── Restore ───────────────────────────────────────────────
    result = restore_backup(
        ctx,
        backup_dir=backup_path,
        db_only=db_only,
        skip_env=skip_env,
    )

    if not result.ok:
        for msg in result.errors:
            error(msg)
        sys.exit(1)

    # ── Sync Postgres password with restored .env ─────────────
    if result.env_restored:
        info("Syncing Postgres password with restored .env…")
        env_path = ctx.env_file or Path(ctx.project_dir) / ".env"
        _sync_postgres_password(ctx, env_path)

    # ── Restart all services ──────────────────────────────────
    console.print("\n[bold cyan]Restarting all services…[/bold cyan]")
    compose_up(ctx, detach=True, wait=True, timeout=120)

    console.print()
    success("Restore complete.")
    if result.databases_restored:
        console.print(f"  Databases: {', '.join(result.databases_restored)}")
    if result.env_restored:
        console.print("  .env: restored")
    if result.volumes_restored:
        console.print(f"  Volumes: {', '.join(result.volumes_restored)}")
