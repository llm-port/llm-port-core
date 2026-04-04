"""``llmport backup`` — create a backup of all databases and config.

Examples:
    llmport backup                          # backup to ./backups
    llmport backup --output-dir /mnt/bak    # custom output directory
    llmport backup --include-volumes        # also snapshot Docker volumes
    llmport backup --db-only                # databases only, skip .env
    llmport backup --retain 3               # keep only 3 most recent
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from llmport.core.backup import create_backup
from llmport.core.compose import build_context_from_config
from llmport.core.console import console, error, success
from llmport.core.settings import load_config


@click.command("backup")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False),
    default="backups",
    show_default=True,
    help="Directory to store backups in.",
)
@click.option(
    "--include-volumes",
    is_flag=True,
    default=False,
    help="Also snapshot named Docker volumes (pg_data, minio_data, …).",
)
@click.option(
    "--retain",
    type=int,
    default=5,
    show_default=True,
    help="Keep only the N most recent backups.",
)
@click.option(
    "--db-only",
    is_flag=True,
    default=False,
    help="Dump databases only (skip .env and volumes).",
)
@click.option("-y", "--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
def backup_cmd(
    *,
    output_dir: str,
    include_volumes: bool,
    retain: int,
    db_only: bool,
    yes: bool,
) -> None:
    """Create a backup of llm.port databases, config, and optionally volumes."""
    console.print("\n[bold magenta]llm.port — Backup[/bold magenta]\n")

    cfg = load_config()
    ctx = build_context_from_config(cfg)

    output_path = Path(output_dir)
    if not output_path.is_absolute():
        output_path = cfg.install_path / output_path

    if not yes:
        click.confirm(
            f"Create backup in {output_path}?",
            default=True,
            abort=True,
        )

    result = create_backup(
        ctx,
        output_dir=output_path,
        include_volumes=include_volumes,
        db_only=db_only,
        retain=retain,
    )

    if not result.ok:
        for msg in result.errors:
            error(msg)
        sys.exit(1)

    console.print()
    success(f"Backup created: {result.backup_dir}")
    console.print(f"  Databases: {', '.join(result.databases)}")
    if result.env_snapshot:
        console.print(f"  .env snapshot: {result.env_snapshot}")
    if result.volumes:
        console.print(f"  Volumes: {', '.join(result.volumes)}")
    if result.manifest_path:
        console.print(f"  Manifest: {result.manifest_path.name}")
