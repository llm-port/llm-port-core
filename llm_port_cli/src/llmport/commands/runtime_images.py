"""``llmport runtime-images``: the runtime images this server holds for its machines.

The same step runs at the end of ``deploy`` and ``upgrade``; this command runs
it on its own -- to retry a pull that failed, to fetch an architecture chosen
later, or (``--check``) to see what is here.
"""

from __future__ import annotations

import sys

import click
from rich.table import Table

from llmport.core import runtime_images as rt
from llmport.core.console import console, info, success, warning
from llmport.core.settings import LlmportConfig, load_config, save_config

_HOW_TO_RETRY = "llmport runtime-images"


def choose(cfg: LlmportConfig, value: str | None) -> set[str] | None:
    """The architectures to fetch: *value* when given (and remembered), else the saved choice.

    Returns None, after saying why, when *value* is not a valid choice.
    """
    try:
        chosen = rt.parse_selection(value if value is not None else cfg.runtime_images)
    except ValueError as exc:
        warning(f"--runtime-images: {exc}")
        return None
    if value is not None and value != cfg.runtime_images:
        cfg.runtime_images = value
        save_config(cfg)
    return chosen


def run_step(cfg: LlmportConfig, *, value: str | None = None) -> bool:
    """Fetch this release's runtime images onto the server and say what happened.

    Never stops the caller: every service is already running without them,
    and a machine can still be served by a cluster peer that has its image.
    Returns whether everything wanted is here.
    """
    chosen = choose(cfg, value)
    if chosen is None:
        return False
    if not chosen:
        console.print("  [dim]Skipping runtime images (runtime_images: none).[/dim]")
        return True

    image = rt.backend_image(cfg.env_path)
    info(f"Runtime images pinned by {image} ({', '.join(sorted(chosen))}):")
    console.print(
        "  [dim]Machines download their runtime image from this server. The first pull is large"
        " (about 10 GB per image); later upgrades fetch only what changed.[/dim]"
    )
    try:
        report = rt.fetch(image, architectures=chosen)
    except RuntimeError as exc:  # docker missing
        warning(f"Could not fetch runtime images: {exc}")
        return False
    if report.error:
        warning(f"Could not fetch runtime images: {report.error}")
        return False

    for outcome in report.outcomes:
        _say(outcome)
    return report.ok


def _say(outcome: rt.Outcome) -> None:
    """One line (or two) per image, in the operator's terms."""
    what = f"{outcome.image.ref} ({outcome.image.arch or 'any architecture'})"
    if outcome.status == "present":
        success(f"{what}: already on this server.")
    elif outcome.status == "pulled":
        success(f"{what}: pulled.")
    elif outcome.status == "skipped":
        console.print(f"  [dim]{what}: skipped, not chosen (--runtime-images).[/dim]")
    elif outcome.status == "not_published":
        warning(
            f"{what}: this release names a build that is on no registry.\n"
            "  Machines of this kind can get it from a cluster peer that has it;"
            " or load it here with: docker load -i <file>"
        )
    elif outcome.status == "mismatch":
        warning(f"{what}: {outcome.detail}; machines would refuse it.")
    else:
        warning(
            f"{what}: pull failed. Retry with: {_HOW_TO_RETRY}\n"
            "  If the registry refused it: the package must be public, or run docker login ghcr.io first."
        )


def _check(cfg: LlmportConfig) -> bool:
    image = rt.backend_image(cfg.env_path)
    try:
        pinned = rt.pinned(image)
    except RuntimeError as exc:
        warning(str(exc))
        return False
    table = Table(title=f"Runtime images pinned by {image}", header_style="bold cyan")
    table.add_column("Architecture")
    table.add_column("Image")
    table.add_column("On this server")
    everything = True
    for runtime in pinned:
        here = rt.local_layers(runtime.ref)
        if here is None:
            state = "[yellow]no[/yellow]"
            everything = False
        elif here == runtime.rootfs_layers:
            state = "[green]yes[/green]"
        else:
            state = "[red]a different build[/red]"
            everything = False
        table.add_row(runtime.arch or "-", runtime.ref, state)
    console.print(table)
    return everything


@click.command("runtime-images")
@click.option(
    "--arch",
    "arch",
    default=None,
    help="Which to fetch: all, none, or architectures such as x86_64,aarch64 (remembered for upgrades).",
)
@click.option("--check", is_flag=True, default=False, help="Only show what this server holds; fetch nothing.")
def runtime_images_cmd(*, arch: str | None, check: bool) -> None:
    r"""Fetch the runtime images machines download from this server.

    \b
    Examples:
        llmport runtime-images                 # fetch what this release pins
        llmport runtime-images --arch x86_64   # only for x86_64 machines, from now on
        llmport runtime-images --check         # what is here, what is missing
    """
    cfg = load_config()
    if not cfg.install_dir:
        warning("No install directory configured. Run 'llmport deploy' first.")
        sys.exit(1)
    ok = _check(cfg) if check else run_step(cfg, value=arch)
    sys.exit(0 if ok else 1)
