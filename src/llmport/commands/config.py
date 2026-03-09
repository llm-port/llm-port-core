"""``llmport config`` — view and manage configuration."""

from __future__ import annotations

from pathlib import Path

import click
import yaml
from rich.panel import Panel
from rich.syntax import Syntax

from llmport.core.console import console, success, error
from llmport.core.settings import load_config, save_config, config_path


@click.group("config")
def config_group() -> None:
    """Manage llmport configuration."""


@config_group.command("show")
def config_show() -> None:
    """Display the current configuration."""
    cfg_path = config_path()

    if not cfg_path.exists():
        console.print(f"[dim]No config file found at {cfg_path}[/dim]")
        console.print("[dim]Using default configuration.[/dim]")
        cfg = load_config()
        import dataclasses

        raw = yaml.dump(dataclasses.asdict(cfg), default_flow_style=False, sort_keys=False)
        console.print(Panel(Syntax(raw, "yaml", theme="monokai"), title="defaults"))
        return

    raw = cfg_path.read_text(encoding="utf-8")
    console.print(
        Panel(
            Syntax(raw, "yaml", theme="monokai"),
            title=str(cfg_path),
            border_style="cyan",
        )
    )


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a configuration value (dot-notation supported).

    Examples:

        llmport config set install_dir /opt/llmport

        llmport config set dev.branch main
    """
    cfg = load_config()
    import dataclasses

    cfg_dict = dataclasses.asdict(cfg)

    # Support dot-notation for nested keys (e.g., dev.branch)
    parts = key.split(".")
    target = cfg_dict
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            error(f"Unknown config section: {part}")
            return
        target = target[part]

    final_key = parts[-1]
    if final_key not in target:
        error(f"Unknown config key: {key}")
        return

    # Coerce value types
    old_val = target[final_key]
    if isinstance(old_val, bool):
        value_typed: str | bool | int | float | list[str] = value.lower() in ("true", "1", "yes")
    elif isinstance(old_val, int):
        value_typed = int(value)
    elif isinstance(old_val, float):
        value_typed = float(value)
    elif isinstance(old_val, list):
        value_typed = [v.strip() for v in value.split(",")]
    else:
        value_typed = value

    target[final_key] = value_typed

    # Rebuild config
    from llmport.core.settings import LlmportConfig, DevConfig

    dev_data = cfg_dict.pop("dev", {}) or {}
    dev_cfg = DevConfig(**{k: v for k, v in dev_data.items() if k in DevConfig.__dataclass_fields__})
    new_cfg = LlmportConfig(**cfg_dict, dev=dev_cfg)

    save_config(new_cfg)
    success(f"Set {key} = {value_typed}")


@config_group.command("path")
def config_path_cmd() -> None:
    """Print the config file path."""
    click.echo(str(config_path()))


@config_group.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing config file.")
def config_init(*, force: bool) -> None:
    """Create a default configuration file."""
    cfg_file = config_path()

    if cfg_file.exists() and not force:
        error(f"Config file already exists at {cfg_file}. Use --force to overwrite.")
        return

    cfg = load_config()
    save_config(cfg)
    success(f"Configuration written to {cfg_file}")


@config_group.command("edit")
def config_edit() -> None:
    """Open the config file in $EDITOR."""
    import os
    import subprocess

    cfg_file = config_path()
    if not cfg_file.exists():
        # Create default first
        cfg = load_config()
        save_config(cfg)

    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))
    if not editor:
        # Fallback based on platform
        import platform

        if platform.system() == "Windows":
            editor = "notepad"
        else:
            editor = "nano"

    subprocess.run([editor, str(cfg_file)], check=False)
