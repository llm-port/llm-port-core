"""``llmport version`` — print version and runtime information."""

from __future__ import annotations

import platform
import sys

import click

from llmport import __version__
from llmport.core.console import console, kv_table
from llmport.core.detect import detect_docker


@click.command("version")
@click.option("--short", is_flag=True, help="Print only the CLI version string.")
def version_cmd(*, short: bool) -> None:
    """Display version and runtime information."""
    if short:
        click.echo(__version__)
        return

    docker = detect_docker()

    rows: list[tuple[str, str]] = [
        ("llmport CLI", __version__),
        ("Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
        ("Platform", platform.platform()),
        ("Docker", docker.version or "[red]not found[/red]"),
        ("Compose", docker.compose_version or "[red]not found[/red]"),
    ]

    kv_table("llm.port CLI", rows)
    console.print()
