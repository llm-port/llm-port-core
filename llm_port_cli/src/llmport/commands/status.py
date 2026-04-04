"""``llmport status`` — show running service status."""

from __future__ import annotations

import click
from rich.table import Table

from llmport.core.compose import build_context_from_config, ps as compose_ps
from llmport.core.console import console
from llmport.core.settings import load_config


def _health_style(health: str) -> str:
    mapping = {
        "healthy": "[green]healthy[/green]",
        "unhealthy": "[red]unhealthy[/red]",
        "starting": "[yellow]starting[/yellow]",
    }
    return mapping.get(health.lower(), health) if health else "—"


def _state_style(state: str) -> str:
    mapping = {
        "running": "[green]running[/green]",
        "exited": "[red]exited[/red]",
        "restarting": "[yellow]restarting[/yellow]",
        "created": "[dim]created[/dim]",
        "dead": "[red]dead[/red]",
    }
    return mapping.get(state.lower(), state) if state else "—"


@click.command("status")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON from docker compose.")
def status_cmd(*, as_json: bool) -> None:
    """Show the status of llm.port services."""
    cfg = load_config()
    ctx = build_context_from_config(cfg)

    services = compose_ps(ctx)

    if as_json:
        import json

        click.echo(json.dumps([s.__dict__ for s in services], indent=2))
        return

    if not services:
        console.print("[dim]No running services found.[/dim]")
        return

    table = Table(
        title="llm.port Services",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Service", style="bold")
    table.add_column("State")
    table.add_column("Health")
    table.add_column("Ports")

    for svc in sorted(services, key=lambda s: s.service):
        table.add_row(
            svc.service,
            _state_style(svc.state),
            _health_style(svc.health),
            svc.ports or "—",
        )

    console.print(table)
    console.print(f"\n[dim]{len(services)} service(s)[/dim]")
