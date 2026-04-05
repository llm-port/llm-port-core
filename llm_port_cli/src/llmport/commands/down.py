"""``llmport down`` — stop llm.port services."""

from __future__ import annotations

import shutil
import subprocess
import sys

import click

from llmport.core.compose import build_context_from_config, down as compose_down
from llmport.core.console import console, success, error, info
from llmport.core.settings import load_config

# Image prefixes that belong to the project (built from source).
_PROJECT_IMAGE_PREFIX = "ghcr.io/llm-port/"


def _remove_project_images() -> int:
    """Remove only locally-built llmport/* images. Return count removed."""
    docker = shutil.which("docker")
    if not docker:
        return 0
    result = subprocess.run(  # noqa: S603
        [docker, "images", "--format", "{{.Repository}}:{{.Tag}}",
         "--filter", f"reference={_PROJECT_IMAGE_PREFIX}*"],
        capture_output=True, text=True,
    )
    images = [img for img in result.stdout.strip().splitlines() if img]
    if not images:
        return 0
    subprocess.run([docker, "image", "rm", "-f", *images],  # noqa: S603
                   capture_output=True, text=True)
    return len(images)


@click.command("down")
@click.option("--volumes/--no-volumes", default=False, help="Remove named volumes declared in the compose file.")
@click.option("--remove-orphans/--no-remove-orphans", default=True, help="Remove containers for services not defined in the compose file.")
@click.option("--all", "nuke", is_flag=True, help="Remove everything: containers, networks, volumes, and built images (keeps third-party images).")
def down_cmd(*, volumes: bool, remove_orphans: bool, nuke: bool) -> None:
    """Stop and remove llm.port containers and networks."""
    cfg = load_config()
    ctx = build_context_from_config(cfg)

    if nuke:
        volumes = True

    console.print("[bold cyan]Stopping llm.port services…[/bold cyan]")

    with console.status("[bold cyan]docker compose down[/bold cyan]"):
        returncode = compose_down(
            ctx,
            volumes=volumes,
            remove_orphans=remove_orphans,
        )

    if returncode == 0:
        success("Services stopped.")
    else:
        error(f"docker compose down exited with code {returncode}.")
        sys.exit(returncode)

    if nuke:
        removed = _remove_project_images()
        if removed:
            info(f"Removed {removed} llmport image(s).")
        else:
            info("No llmport images to remove.")

        # Stop and remove the local node agent if present.
        from llmport.core.local_node import remove_local_node_agent  # noqa: PLC0415

        workspace = cfg.install_path.parent
        remove_local_node_agent(workspace=workspace)
