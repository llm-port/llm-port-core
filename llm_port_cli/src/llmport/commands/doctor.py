"""``llmport doctor`` — diagnose the host environment.

Runs comprehensive checks on the system and reports whether all
prerequisites are satisfied for running llm.port.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from llmport.core.console import console
from llmport.core.detect import (
    check_install_ports,
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
        report = full_report(check_ports=False)
        port_checks = check_install_ports(*_install_compose()) if ports else []

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
    # An "8 GB" machine reports 7.5-7.8 GB once the kernel has its share;
    # the release test VM ran everything in 7 GB.
    ram_ok = ram.total_gb >= 7.0
    console.print(
        f"\n{_check_mark(ram_ok)}  RAM: [bold]{ram.total_gb:.1f} GB[/bold] total, "
        f"{ram.available_gb:.1f} GB available  "
        f"{'(8 GB recommended)' if not ram_ok else ''}"
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
    # The server needs no GPU: models run on the machines added to it.
    gpu = report.gpu
    if gpu.has_gpu:
        for dev in gpu.devices:
            console.print(
                f"[cyan]i[/cyan]  GPU: [bold]{dev.name}[/bold] "
                f"({dev.vram_mb} MB) -- add this server as a machine to use it"
            )
    else:
        console.print("[cyan]i[/cyan]  GPU: none on this server (models run on the machines you add)")

    # ── Ports ─────────────────────────────────────────────────────
    # Developer tools (git, uv, node) are `llmport dev doctor`'s: a server
    # running the published images needs none of them.
    conflicts = [pc for pc in port_checks if pc.in_use and not pc.ours]
    if port_checks:
        console.print()
        port_table = Table(title="Ports this install publishes", show_header=True, header_style="bold")
        port_table.add_column("Port", justify="right")
        port_table.add_column("Service")
        port_table.add_column("Status")

        for pc in port_checks:
            if not pc.in_use:
                status = "[green]available[/green]"
            elif pc.ours:
                status = "[green]LLM.Port[/green]"
            else:
                status = "[red]in use by something else[/red]"
            port_table.add_row(str(pc.port), pc.label, status)
        console.print(port_table)
        if conflicts:
            console.print(
                "[dim]  A port held by something else stops `llmport deploy`: free it, or change\n"
                "  the port in .env (LLM_PORT_HTTP_PORT, PROM_PORT, ...).[/dim]"
            )

    # ── Verdict ───────────────────────────────────────────────────
    all_ok = (
        docker.installed
        and docker.compose_installed
        and docker.daemon_running
        and ram_ok
        and disk_ok
        and not conflicts
    )
    console.print()
    if all_ok:
        console.print("[bold green]All core prerequisites met.[/bold green] ✨")
    else:
        console.print("[bold red]Some prerequisites are missing — see above.[/bold red]")

    console.print("[dim]Developer tools: llmport dev doctor[/dim]")
    if verbose:
        console.print(f"\n[dim]Report generated with {len(port_checks)} port checks.[/dim]")


def _install_compose() -> tuple[Path | None, Path | None]:
    """The compose file and .env of the configured install, or of this CLI's release."""
    from llmport.core.bundle import bundled_files  # noqa: PLC0415
    from llmport.core.settings import load_config  # noqa: PLC0415

    cfg = load_config()
    if cfg.install_dir and cfg.compose_path.is_file():
        return cfg.compose_path, cfg.env_path
    bundled = bundled_files()
    if bundled is not None:
        return Path(str(bundled / "docker-compose.yaml")), None
    return None, None
