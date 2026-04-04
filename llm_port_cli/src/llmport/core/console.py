"""Rich console singleton and shared output helpers.

Provides a single ``Console`` instance used by every command so that
output is consistent (colours, width, Unicode support).  Helper
functions wrap common patterns: success/warning/error panels, spinners,
tables, and key-value displays.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

_THEME = Theme(
    {
        "info": "cyan",
        "success": "bold green",
        "warning": "bold yellow",
        "error": "bold red",
        "heading": "bold magenta",
        "muted": "dim",
    },
)

console = Console(theme=_THEME)


# ── Shorthand helpers ─────────────────────────────────────────────


def heading(text: str) -> None:
    """Print a heading-styled line."""
    console.print(f"\n[heading]{text}[/heading]")


def success(text: str) -> None:
    """Print a success-styled line."""
    console.print(f"[success]✓[/success] {text}")


def warning(text: str) -> None:
    """Print a warning-styled line."""
    console.print(f"[warning]⚠[/warning] {text}")


def error(text: str) -> None:
    """Print an error-styled line."""
    console.print(f"[error]✗[/error] {text}")


def info(text: str) -> None:
    """Print an info-styled line."""
    console.print(f"[info]ℹ[/info] {text}")


def kv_table(title: str, rows: list[tuple[str, str]]) -> None:
    """Print a two-column key/value table."""
    table = Table(title=title, show_header=False, border_style="dim")
    table.add_column("Key", style="bold", min_width=20)
    table.add_column("Value")
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)


def banner(lines: list[str], *, title: str = "llm.port", border_style: str = "cyan") -> None:
    """Print a bordered panel with the given lines."""
    body = "\n".join(lines)
    console.print(Panel(body, title=f"[bold]{title}[/bold]", border_style=border_style))


def service_table(rows: list[dict[str, str]], *, title: str = "Services") -> None:
    """Print a services status table.

    Each row dict should have keys: name, status, health, ports, uptime.
    """
    table = Table(title=title, border_style="dim")
    table.add_column("Service", style="bold")
    table.add_column("Status")
    table.add_column("Health")
    table.add_column("Ports", style="cyan")
    table.add_column("Uptime", style="dim")

    status_styles = {
        "running": "green",
        "exited": "red",
        "created": "yellow",
        "restarting": "yellow",
        "paused": "yellow",
        "removing": "red",
        "dead": "red",
    }

    for row in rows:
        status = row.get("status", "unknown")
        style = status_styles.get(status, "white")
        table.add_row(
            row.get("name", ""),
            f"[{style}]{status}[/{style}]",
            row.get("health", ""),
            row.get("ports", ""),
            row.get("uptime", ""),
        )
    console.print(table)
