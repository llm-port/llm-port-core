"""``llmport backup`` — create a backup of all databases and config.

Examples:
    llmport backup                          # backup to ./backups
    llmport backup --output-dir /mnt/bak    # custom output directory
    llmport backup --include-volumes        # also snapshot Docker volumes
    llmport backup --db-only                # databases only, skip .env
    llmport backup --retain 3               # keep only 3 most recent
    llmport backup schedule                 # every night at 03:00, keep 7
    llmport backup schedule --off           # stop the nightly backup
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from llmport.core.backup import create_backup
from llmport.core.compose import build_context_from_config
from llmport.core.console import console, error, info, success, warning
from llmport.core.settings import load_config


@click.group("backup", invoke_without_command=True)
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
@click.pass_context
def backup_cmd(
    click_ctx: click.Context,
    *,
    output_dir: str,
    include_volumes: bool,
    retain: int,
    db_only: bool,
    yes: bool,
) -> None:
    """Create a backup of llm.port databases, config, and optionally volumes."""
    if click_ctx.invoked_subcommand is not None:
        return
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


@backup_cmd.command("schedule")
@click.option("--at", "at", default="03:00", show_default=True, help="Time of day, HH:MM (server time).")
@click.option("--retain", type=int, default=7, show_default=True, help="Keep the N most recent backups.")
@click.option("--off", is_flag=True, default=False, help="Stop the nightly backup.")
def schedule_cmd(*, at: str, retain: int, off: bool) -> None:
    """Back up every night, keeping the most recent few.

    The backups land in <install_dir>/backups/, on the same disk as the
    data: copy them elsewhere too (or pass --output-dir to a mounted disk
    with `llmport backup` from your own scheduler).
    """
    from llmport.core import schedule  # noqa: PLC0415

    if not schedule.available():
        error("No crontab on this machine: schedule `llmport backup -y` with your own scheduler.")
        sys.exit(1)
    if off:
        if schedule.remove():
            success("Nightly backup stopped.")
        else:
            info("No nightly backup was scheduled.")
        return
    try:
        hour, minute = (int(part) for part in at.split(":", 1))
        if not (0 <= hour < 24 and 0 <= minute < 60):  # noqa: PLR2004
            raise ValueError
    except ValueError:
        error(f"--at wants HH:MM, got {at!r}.")
        sys.exit(2)
    if retain < 1:
        error("--retain must keep at least one backup.")
        sys.exit(2)
    cfg = load_config()
    if not cfg.install_dir:
        error("No install to back up: run `llmport deploy` first.")
        sys.exit(1)
    backups = cfg.install_path / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    schedule.install(hour=hour, minute=minute, retain=retain, log=backups / "backup.log")
    success(f"Backing up every night at {hour:02d}:{minute:02d}, keeping {retain}: {backups}")
    warning("The backups are on the same disk as the data; copy them elsewhere too.")
