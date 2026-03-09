"""``llmport logs`` — stream container logs."""

from __future__ import annotations

import sys

import click

from llmport.core.compose import build_context_from_config, logs as compose_logs
from llmport.core.settings import load_config


@click.command("logs")
@click.option("-f", "--follow", is_flag=True, help="Follow log output.")
@click.option("-n", "--tail", default=100, show_default=True, help="Number of lines to show from the end.")
@click.option("--timestamps/--no-timestamps", default=False, help="Show timestamps.")
@click.argument("services", nargs=-1)
def logs_cmd(
    *,
    follow: bool,
    tail: int,
    timestamps: bool,
    services: tuple[str, ...],
) -> None:
    """View logs from llm.port services.

    Pass one or more SERVICE names to filter, or omit to see all.
    """
    cfg = load_config()
    ctx = build_context_from_config(cfg)

    returncode = compose_logs(
        ctx,
        services=list(services) if services else None,
        follow=follow,
        tail=tail,
        timestamps=timestamps,
    )

    if returncode != 0:
        sys.exit(returncode)
