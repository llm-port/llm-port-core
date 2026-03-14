"""``llmport rebuild`` — rebuild and redeploy individual services.

Rebuild one or more services without touching the rest of the stack.
Useful for developers who changed frontend code, backend code, etc.
and want a quick turnaround without a full ``llmport deploy``.

Examples:
    llmport rebuild frontend          # rebuild + restart frontend only
    llmport rebuild backend api       # rebuild backend and api
    llmport rebuild --all             # rebuild all app services
"""

from __future__ import annotations

import sys

import click

from llmport.core.compose import (
    ComposeContext,
    build as compose_build,
    build_context_from_config,
    up as compose_up,
)
from llmport.core.console import console, error, info, success, warning
from llmport.core.settings import load_config


# Map friendly names → compose service names.
# Keeps the CLI developer-friendly (``llmport rebuild frontend``)
# while resolving to the actual compose service name.
SERVICE_ALIASES: dict[str, list[str]] = {
    "frontend": ["llm-port-frontend"],
    "backend": ["llm-port-backend", "llm-port-backend-worker"],
    "api": ["llm-port-api"],
    "mcp": ["llm-port-mcp"],
    "pii": ["llm-port-pii", "llm-port-pii-worker"],
    "rag": ["llm-port-rag"],
    "auth": ["llm-port-auth"],
    "mailer": ["llm-port-mailer"],
    "docling": ["llm-port-docling"],
    "nginx": ["llm-port-nginx"],
}


def _resolve_services(names: tuple[str, ...]) -> list[str]:
    """Resolve friendly names to compose service names."""
    resolved: list[str] = []
    for name in names:
        name = name.lower().strip()
        if name in SERVICE_ALIASES:
            resolved.extend(SERVICE_ALIASES[name])
        elif name.startswith("llm-port-"):
            resolved.append(name)
        else:
            # Try with prefix
            prefixed = f"llm-port-{name}"
            resolved.append(prefixed)
    return list(dict.fromkeys(resolved))  # dedupe, preserve order


@click.command("rebuild")
@click.argument("services", nargs=-1)
@click.option("--all", "rebuild_all", is_flag=True, help="Rebuild all application services.")
@click.option("--no-cache", is_flag=True, help="Build without Docker cache.")
@click.option("--no-deps", is_flag=True, default=True, help="Don't restart dependent services (default: true).")
@click.option("--with-deps", is_flag=True, help="Also restart dependent services.")
def rebuild_cmd(
    services: tuple[str, ...],
    *,
    rebuild_all: bool,
    no_cache: bool,
    no_deps: bool,
    with_deps: bool,
) -> None:
    """Rebuild and redeploy specific services.

    Pass one or more service names (e.g. frontend, backend, api, mcp).
    The service image is rebuilt from source and the container is
    recreated with the latest .env values — no full redeploy needed.

    \b
    Examples:
        llmport rebuild frontend           # just the React UI
        llmport rebuild backend            # backend + worker
        llmport rebuild backend api        # multiple services
        llmport rebuild --all              # all app images
        llmport rebuild frontend --no-cache  # clean build
    """
    if not services and not rebuild_all:
        # Show available aliases
        console.print("[bold cyan]Available services:[/bold cyan]")
        for alias, targets in sorted(SERVICE_ALIASES.items()):
            console.print(f"  [bold]{alias:12s}[/bold] → {', '.join(targets)}")
        console.print("\n[dim]Usage: llmport rebuild <service> [service...][/dim]")
        console.print("[dim]       llmport rebuild --all[/dim]")
        return

    cfg = load_config()
    ctx = build_context_from_config(cfg)

    if rebuild_all:
        # Build all services that have a build context
        target_services = None  # compose build with no args = build all
        info("Rebuilding all application images…")
    else:
        target_services = _resolve_services(services)
        info(f"Rebuilding: {', '.join(target_services)}")

    # Step 1: Build
    console.print("\n[bold cyan]Building images…[/bold cyan]")
    rc = compose_build(ctx, services=target_services, no_cache=no_cache)
    if rc != 0:
        error(f"Build failed (exit code {rc}).")
        sys.exit(rc)
    success("Images built.")

    # Step 2: Recreate containers (picks up new image + new .env)
    console.print("\n[bold cyan]Recreating containers…[/bold cyan]")

    cmd = ctx.base_cmd() + ["up", "-d", "--force-recreate"]
    if no_deps and not with_deps:
        cmd.append("--no-deps")
    if target_services:
        cmd.extend(target_services)

    import subprocess
    result = subprocess.run(cmd, check=False)  # noqa: S603
    if result.returncode != 0:
        error(f"Container recreation failed (exit code {result.returncode}).")
        sys.exit(result.returncode)

    success("Services rebuilt and redeployed.")
    if target_services:
        console.print(f"  [dim]Rebuilt: {', '.join(target_services)}[/dim]")
    console.print("  [dim]Tip: use 'llmport logs <service>' to follow logs.[/dim]")
