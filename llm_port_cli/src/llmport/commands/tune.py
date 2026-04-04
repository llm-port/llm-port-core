"""``llmport tune`` — auto-detect system resources and generate scalability settings."""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from llmport.core.console import console, success
from llmport.core.env_gen import read_env_file, write_env_file
from llmport.core.settings import load_config
from llmport.core.sysinfo import (
    ALL_SERVICES,
    DB_SERVICES,
    DEFAULT_RESOURCE_PCT,
    RABBIT_SERVICES,
    calculate_tune_profile,
    detect_system,
)


@click.command("tune")
@click.option(
    "--profile",
    type=click.Choice(["dev", "prod"]),
    default="dev",
    help="Tuning profile: dev (conservative) or prod (production-grade).",
)
@click.option(
    "--resource-pct",
    type=click.FloatRange(0.1, 1.0),
    default=DEFAULT_RESOURCE_PCT,
    show_default=True,
    help="Fraction of host CPU to allocate to the control plane (prod only).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show computed values without writing to .env.",
)
def tune_cmd(*, profile: str, resource_pct: float, dry_run: bool) -> None:
    """Detect host resources and write optimal scalability settings to .env."""
    sys_info = detect_system()

    # ── Display detected hardware ────────────────────────────
    hw_table = Table(title="Detected Hardware", show_header=False, title_style="bold cyan")
    hw_table.add_column("Metric", style="dim")
    hw_table.add_column("Value", style="bold")
    hw_table.add_row("Physical CPU cores", str(sys_info.physical_cores))
    hw_table.add_row("Logical CPU cores", str(sys_info.logical_cores))
    hw_table.add_row("Total RAM", f"{sys_info.total_ram_gb} GB")
    hw_table.add_row("Profile", profile)
    if profile == "prod":
        budget = max(1, int(sys_info.physical_cores * resource_pct))
        hw_table.add_row("Resource budget", f"{int(resource_pct * 100)}% ({budget} cores)")
    console.print(hw_table)
    console.print()

    # ── Compute tune profile ─────────────────────────────────
    tp = calculate_tune_profile(profile, system=sys_info, resource_pct=resource_pct)

    # ── Display computed values ──────────────────────────────
    svc_table = Table(title="Computed Scalability Settings", title_style="bold cyan")
    svc_table.add_column("Service", style="bold")
    svc_table.add_column("Workers", justify="right")
    svc_table.add_column("DB Pool", justify="right")
    svc_table.add_column("DB Overflow", justify="right")
    svc_table.add_column("RMQ Pool", justify="right")
    svc_table.add_column("RMQ Channels", justify="right")

    for svc in ALL_SERVICES:
        svc_table.add_row(
            svc,
            str(tp.workers[svc]),
            str(tp.db_pool_size[svc]) if svc in DB_SERVICES else "—",
            str(tp.db_max_overflow[svc]) if svc in DB_SERVICES else "—",
            str(tp.rabbit_pool_size[svc]) if svc in RABBIT_SERVICES else "—",
            str(tp.rabbit_channel_pool_size[svc]) if svc in RABBIT_SERVICES else "—",
        )

    console.print(svc_table)
    console.print()

    if dry_run:
        console.print("[dim]Dry run — no files written.[/dim]")
        return

    # ── Write to .env ────────────────────────────────────────
    cfg = load_config()
    env_path = Path(cfg.install_dir) / ".env"

    # Merge: read existing → overlay tune vars → write back.
    existing = read_env_file(env_path)
    existing.update(tp.to_env_dict())
    write_env_file(env_path, existing, preserve_secrets=True)

    success(f"Scalability settings written to {env_path}")
