"""``llmport module`` — enable / disable optional platform modules."""

from __future__ import annotations

import click
from rich.table import Table

from llmport.core.console import console, success, warning
from llmport.core.registry import MODULES_COMPAT as MODULES
from llmport.core.settings import load_config, save_config


@click.group("module")
def module_group() -> None:
    """Manage optional llm.port modules (pii, auth, mailer, docling)."""


@module_group.command("list")
def module_list() -> None:
    """List available modules and their status."""
    cfg = load_config()
    active_profiles = set(cfg.profiles or [])

    table = Table(
        title="llm.port Modules",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Module", style="bold")
    table.add_column("Status")
    table.add_column("Description")

    for name, info in MODULES.items():
        enabled = info["profile"] in active_profiles
        status = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
        table.add_row(name, status, info["description"])

    console.print(table)


@module_group.command("enable")
@click.argument("modules", nargs=-1, required=True)
def module_enable(modules: tuple[str, ...]) -> None:
    """Enable one or more modules.

    Example: llmport module enable pii auth
    """
    cfg = load_config()
    profiles = set(cfg.profiles or [])

    for mod in modules:
        mod_lower = mod.lower()
        if mod_lower not in MODULES:
            warning(f"Unknown module: {mod_lower}. Available: {', '.join(MODULES)}")
            continue
        profile = MODULES[mod_lower]["profile"]
        profiles.add(profile)
        success(f"Enabled module: {mod_lower}")

    cfg.profiles = sorted(profiles)
    save_config(cfg)
    console.print("\n[dim]Run [bold]llmport up[/bold] to apply changes.[/dim]")


@module_group.command("disable")
@click.argument("modules", nargs=-1, required=True)
def module_disable(modules: tuple[str, ...]) -> None:
    """Disable one or more modules.

    Example: llmport module disable pii
    """
    cfg = load_config()
    profiles = set(cfg.profiles or [])

    for mod in modules:
        mod_lower = mod.lower()
        if mod_lower not in MODULES:
            warning(f"Unknown module: {mod_lower}. Available: {', '.join(MODULES)}")
            continue
        profile = MODULES[mod_lower]["profile"]
        profiles.discard(profile)
        success(f"Disabled module: {mod_lower}")

    cfg.profiles = sorted(profiles)
    save_config(cfg)
    console.print("\n[dim]Run [bold]llmport down && llmport up[/bold] to apply changes.[/dim]")
