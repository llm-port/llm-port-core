"""``llmport doctor`` — diagnose the host environment.

Runs comprehensive checks on the system and reports whether all
prerequisites are satisfied for running llm.port.
"""

from __future__ import annotations

import click
from rich.panel import Panel
from rich.table import Table

from llmport.core.console import console
from llmport.core.detect import (
    full_report,
)


def _check_mark(ok: bool) -> str:
    return "[green]✓[/green]" if ok else "[red]✗[/red]"


@click.command("doctor")
@click.option("--ports/--no-ports", default=True, help="Check port availability.")
@click.pass_context
def doctor_cmd(ctx: click.Context, *, ports: bool) -> None:
    """Run system health checks and report readiness."""
    verbose = ctx.obj.get("verbose", False)

    with console.status("[bold cyan]Running diagnostics…[/bold cyan]"):
        report = full_report(check_ports=ports)

    # ── OS ────────────────────────────────────────────────────────
    os_info = report.os
    console.print(
        Panel(
            f"[bold]{os_info.system}[/bold] {os_info.release} — {os_info.machine}",
            title="Operating System",
            border_style="cyan",
        )
    )

    # ── RAM ───────────────────────────────────────────────────────
    ram = report.ram
    ram_ok = ram.total_gb >= 8.0
    console.print(
        f"\n{_check_mark(ram_ok)}  RAM: [bold]{ram.total_gb:.1f} GB[/bold] total, "
        f"{ram.available_gb:.1f} GB available  "
        f"{'(≥8 GB recommended)' if not ram_ok else ''}"
    )

    # ── Disk ──────────────────────────────────────────────────────
    disk = report.disk
    disk_ok = disk.free_gb >= 20.0
    console.print(
        f"{_check_mark(disk_ok)}  Disk: [bold]{disk.free_gb:.1f} GB[/bold] free of "
        f"{disk.total_gb:.1f} GB  "
        f"{'(≥20 GB recommended)' if not disk_ok else ''}"
    )

    # ── Docker ────────────────────────────────────────────────────
    docker = report.docker
    docker_ver = docker.version or f'[red]not found[/red]  → {docker.install_hint}'
    console.print(
        f"\n{_check_mark(docker.installed)}  Docker: "
        f"{docker_ver}"
    )
    if docker.installed and not docker.daemon_running and docker.error:
        console.print(f"   [dim]{docker.error}[/dim]")
    compose_ver = docker.compose_version or f'[red]not found[/red]  → {docker.install_hint}'
    console.print(
        f"{_check_mark(docker.compose_installed)}  Compose: "
        f"{compose_ver}"
    )
    console.print(
        f"{_check_mark(docker.daemon_running)}  Docker daemon: "
        f"{'running' if docker.daemon_running else '[red]not running[/red]'}"
    )

    # ── GPU ───────────────────────────────────────────────────────
    gpu = report.gpu
    if gpu.has_gpu:
        for dev in gpu.devices:
            console.print(
                f"{_check_mark(True)}  GPU: [bold]{dev.name}[/bold] "
                f"({dev.vram_mb} MB)"
            )
    else:
        console.print(f"{_check_mark(False)}  GPU: not detected (CPU-only mode)")

    # ── Dev tools ─────────────────────────────────────────────────
    if report.tools:
        console.print()
        tools_table = Table(title="Developer Tools", show_header=True, header_style="bold")
        tools_table.add_column("Tool", style="bold")
        tools_table.add_column("Status")
        tools_table.add_column("Version")
        tools_table.add_column("Install")

        for tool in report.tools:
            if tool.found:
                status = _check_mark(True)
                install_col = ""
            else:
                status = _check_mark(False)
                install_col = tool.install_hint or "—"
            tools_table.add_row(
                tool.name,
                status,
                tool.version or "—",
                install_col,
            )
        console.print(tools_table)

    # ── Ports ─────────────────────────────────────────────────────
    if ports and report.ports:
        console.print()
        port_table = Table(title="Port Availability", show_header=True, header_style="bold")
        port_table.add_column("Port", justify="right")
        port_table.add_column("Service")
        port_table.add_column("Status")

        for pc in report.ports:
            status = (
                "[green]available[/green]"
                if not pc.in_use
                else "[red]in use[/red]"
            )
            port_table.add_row(str(pc.port), pc.label, status)
        console.print(port_table)

    # ── Verdict ───────────────────────────────────────────────────
    all_ok = (
        docker.installed
        and docker.compose_installed
        and docker.daemon_running
        and ram_ok
        and disk_ok
    )
    console.print()
    if all_ok:
        console.print("[bold green]All core prerequisites met.[/bold green] ✨")
    else:
        console.print("[bold red]Some prerequisites are missing — see above.[/bold red]")

    if verbose:
        console.print(f"\n[dim]Report generated with {len(report.ports)} port checks.[/dim]")
