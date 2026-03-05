"""``llmport dev status`` — show developer workspace status."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
from rich.table import Table

from llmport.core.compose import ComposeContext, ps as compose_ps
from llmport.core.console import console, info
from llmport.core.git import current_branch
from llmport.core.registry import DEV_PROCESSES
from llmport.core.settings import REPO_DIR_MAP, load_config

from .dev_group import dev_group


def _check_process(pattern: str) -> bool:
    """Check if a process matching the pattern is running."""
    import platform

    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["powershell", "-Command",
                 f"Get-Process | Where-Object {{ $_.CommandLine -like '*{pattern}*' }} | "
                 f"Select-Object -First 1 | ForEach-Object {{ $_.Id }}"],
                capture_output=True,
                text=True,
            )
            return bool(result.stdout.strip())
        else:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
            )
            return result.returncode == 0
    except Exception:
        return False


@dev_group.command("status")
def dev_status() -> None:
    """Show the status of the developer workspace.

    Displays:
      • Repository branches and git status
      • Shared infrastructure containers
      • Running dev processes (backend, worker, frontend)
    """
    cfg = load_config()
    workspace = Path(cfg.dev.workspace_dir) if cfg.dev and cfg.dev.workspace_dir else Path.cwd()

    console.print("[bold magenta]llm.port Developer Status[/bold magenta]\n")

    # ── Repositories ──────────────────────────────────────────────
    repo_table = Table(title="Repositories", show_header=True, header_style="bold cyan")
    repo_table.add_column("Repository", style="bold")
    repo_table.add_column("Branch")
    repo_table.add_column("Status")

    for _gh_name, local_name in sorted(REPO_DIR_MAP.items()):
        repo_path = workspace / local_name
        if repo_path.exists():
            branch = current_branch(repo_path) or "—"
            # Quick dirty check
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(repo_path),
                    capture_output=True,
                    text=True,
                )
                if result.stdout.strip():
                    status = f"[yellow]{len(result.stdout.strip().splitlines())} changed[/yellow]"
                else:
                    status = "[green]clean[/green]"
            except Exception:
                status = "[dim]unknown[/dim]"

            repo_table.add_row(local_name, branch, status)
        else:
            repo_table.add_row(local_name, "—", "[dim]not cloned[/dim]")

    console.print(repo_table)

    # ── Shared infrastructure ─────────────────────────────────────
    console.print()
    from llmport.commands.dev.dev_init import _resolve_shared_compose

    compose_file = _resolve_shared_compose(workspace)
    if compose_file:
        env_file = compose_file.parent / ".env"
        ctx = ComposeContext(
            compose_files=[str(compose_file)],
            env_file=str(env_file) if env_file.exists() else None,
            project_dir=str(compose_file.parent),
        )
        services = compose_ps(ctx)

        if services:
            svc_table = Table(title="Shared Infrastructure", show_header=True, header_style="bold cyan")
            svc_table.add_column("Container", style="bold")
            svc_table.add_column("State")
            svc_table.add_column("Health")
            svc_table.add_column("Ports")

            state_styles = {
                "running": "[green]running[/green]",
                "exited": "[red]exited[/red]",
                "restarting": "[yellow]restarting[/yellow]",
            }
            health_styles = {
                "healthy": "[green]healthy[/green]",
                "unhealthy": "[red]unhealthy[/red]",
                "starting": "[yellow]starting[/yellow]",
            }

            for svc in sorted(services, key=lambda s: s.service):
                svc_table.add_row(
                    svc.service,
                    state_styles.get(svc.state, svc.state or "—"),
                    health_styles.get(svc.health, svc.health or "—"),
                    svc.ports or "—",
                )
            console.print(svc_table)
        else:
            info("No shared infrastructure containers running.")
    else:
        info("Shared compose file not found.")

    # ── Dev processes ─────────────────────────────────────────────
    console.print()
    proc_table = Table(title="Dev Processes", show_header=True, header_style="bold cyan")
    proc_table.add_column("Process", style="bold")
    proc_table.add_column("Status")
    proc_table.add_column("URL")

    for proc in DEV_PROCESSES:
        running = _check_process(proc.pattern)
        status = "[green]running[/green]" if running else "[dim]stopped[/dim]"
        proc_table.add_row(proc.name, status, proc.url)

    console.print(proc_table)
