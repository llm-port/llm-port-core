"""``llmport dev pull`` — pull the latest changes from a branch.

Fetches and fast-forwards every repository checked out in the dev
workspace to the latest commit of the target branch (default: ``dev``).
This is the counterpart of ``dev init --overwrite`` for an already
bootstrapped workspace: no cloning and no dependency installation — it
just brings the checkouts up to date.

Repositories discovered in the workspace (same convention as
``dev status``):

* every llm-port service in the monorepo layout
  ``<workspace>/llm-port-core/<name>``, or
* flat ``<workspace>/<name>`` checkouts, when that layout is in use;
* the workspace root itself, if it is a git checkout (running the CLI
  from inside a checkout).

Subdirectories sharing a git repository collapse to the single repo
root, and only repositories that live *inside* the workspace are ever
touched — unrelated checkouts (e.g. an ``nvm`` install under a
home-directory workspace) and ancestor checkouts the workspace merely
sits in are both excluded.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click
from rich.table import Table

from llmport.core.console import console, error, info, success, warning
from llmport.core.registry import REPO_DIR_MAP
from llmport.core.settings import load_config
from llmport.core.workspace import find_service_dir

from .dev_group import dev_group


def _git(repo_dir: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess | None:
    """Run a git command in *repo_dir*; returns None if git is missing or timed out."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        return subprocess.run(  # noqa: S603
            [git, *args],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


def _git_toplevel(path: Path, within: Path | None = None) -> Path | None:
    """Return the git repo root containing *path*, or None if it is not in one.

    When *within* is given, the repo root must lie inside *within*
    (inclusive) — ``rev-parse --show-toplevel`` walks up ancestor
    directories, so a candidate inside a checkout merely *containing*
    the workspace would otherwise resolve to that outer repo.
    """
    if not path.is_dir():
        return None
    proc = _git(path, "rev-parse", "--show-toplevel", timeout=15)
    if proc is None or proc.returncode != 0:
        return None
    toplevel = Path(proc.stdout.strip())
    if within is not None:
        try:
            toplevel.resolve().relative_to(within.resolve())
        except ValueError:
            return None
    return toplevel


def _find_repo_dirs(workspace: Path) -> list[tuple[str, Path]]:
    """Return ``(display_name, repo_root)`` for each repo in the workspace.

    Discovery follows the ``dev status`` convention — registry-driven via
    :func:`find_service_dir` (monorepo layout ``<ws>/llm-port-core/<name>``
    or flat ``<ws>/<name>``) — plus the workspace root itself when it is a
    checkout (running the CLI from inside a repo).  Candidates that live
    inside the same git repository (e.g. monorepo subdirs) collapse to the
    single repo root, so each checkout is pulled exactly once.  Unrelated
    repos that happen to sit in the workspace (e.g. ``.nvm``) are never
    touched.
    """
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def _add(candidate: Path) -> None:
        toplevel = _git_toplevel(candidate, within=workspace)
        if toplevel is None:
            return
        key = str(toplevel.resolve())
        if key in seen:
            return
        seen.add(key)
        found.append((toplevel.name or str(toplevel), toplevel))

    if workspace.is_dir():
        _add(workspace)
    for local_name in REPO_DIR_MAP.values():
        _add(find_service_dir(workspace, local_name))

    return found


def _pull_repo(repo_dir: Path, branch: str, force: bool) -> tuple[str, str]:
    """Pull *branch* into *repo_dir*.

    Returns a ``(status, detail)`` tuple where status is one of
    ``up-to-date``, ``updated``, ``failed``, ``skipped`` and detail is a
    human-readable explanation (current branch, error message, …).
    """
    proc = _git(repo_dir, "fetch", "origin", "--prune")
    if proc is None:
        return "failed", "git not found on PATH (or fetch timed out)"
    if proc.returncode != 0:
        return "failed", (proc.stderr or proc.stdout).strip() or f"git fetch exited with code {proc.returncode}"

    # Does the branch even exist on the remote?
    check = _git(repo_dir, "rev-parse", "--verify", "--quiet", f"origin/{branch}")
    if check is not None and check.returncode != 0:
        return "skipped", f"branch '{branch}' not found on remote"

    cur_proc = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    current = cur_proc.stdout.strip() if cur_proc and cur_proc.returncode == 0 else "unknown"

    if current != branch:
        if force:
            co = _git(repo_dir, "checkout", branch)
            if co is None or co.returncode != 0:
                detail = (co.stderr if co else "git timed out").strip() or "git checkout failed"
                return "failed", detail
        else:
            return "skipped", f"checked out on '{current}' (use --force to switch to '{branch}')"

    pull = _git(repo_dir, "pull", "--ff-only", "origin", branch)
    if pull is None:
        return "failed", "git pull timed out"
    if pull.returncode != 0:
        detail = (pull.stderr or pull.stdout).strip()
        if "not possible" in detail or "diverged" in detail.lower() or "conflict" in detail.lower():
            return "failed", "local changes conflict with origin — commit or stash them, then re-run"
        return "failed", detail or f"git pull exited with code {pull.returncode}"

    if "Already up to date" in pull.stdout:
        return "up-to-date", f"on '{branch}'"
    return "updated", f"on '{branch}'"


@dev_group.command("pull")
@click.option(
    "--branch",
    "-b",
    default="dev",
    show_default=True,
    help="Branch to pull from the remote.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Switch repos to the target branch even if another branch is checked out.",
)
def dev_pull(*, branch: str, force: bool) -> None:
    """Pull the latest changes from the dev branch.

    Fetches and fast-forwards every repository checked out in the dev
    workspace to the latest commit of the target branch (default
    ``dev``).  Working trees that cannot be fast-forwarded are reported
    and left untouched.

    \b
    Examples:
        llmport dev pull
        llmport dev pull --branch master
        llmport dev pull -b dev --force
    """
    cfg = load_config()
    workspace = (
        Path(cfg.dev.workspace_dir).expanduser()
        if cfg.dev and cfg.dev.workspace_dir
        else Path.cwd()
    )

    console.print(f"\n[bold cyan]Pulling [bold]{branch}[/bold] in {workspace}[/bold cyan]\n")
    if not workspace.is_dir():
        error(f"Dev workspace does not exist: {workspace}")
        raise SystemExit(1)

    repos = _find_repo_dirs(workspace)
    if not repos:
        error(
            f"No git repositories found in {workspace}. "
            "Run [bold]llmport dev init[/bold] to bootstrap the workspace first."
        )
        raise SystemExit(1)

    table = Table(title="Pull results", show_header=True, header_style="bold cyan")
    table.add_column("Repository", style="bold")
    table.add_column("Result")
    table.add_column("Detail")

    style_by_status = {
        "up-to-date": "[green]up to date[/green]",
        "updated": "[green]updated[/green]",
        "failed": "[red]failed[/red]",
        "skipped": "[yellow]skipped[/yellow]",
    }
    counts: dict[str, int] = {}

    for name, repo_dir in repos:
        status, detail = _pull_repo(repo_dir, branch, force)
        counts[status] = counts.get(status, 0) + 1
        table.add_row(name, style_by_status.get(status, status), detail or "—")

    console.print(table)

    failed = counts.get("failed", 0)
    if counts.get("updated", 0):
        success(f"{counts['updated']} repository(ies) updated to [bold]{branch}[/bold].")
    if counts.get("up-to-date", 0):
        info(f"{counts['up-to-date']} repository(ies) already up to date.")
    if counts.get("skipped", 0):
        warning(
            f"{counts['skipped']} repository(ies) skipped "
            "(branch missing on remote or checked out on another branch)."
        )
    if failed:
        error(f"{failed} repository(ies) failed — resolve the reported issues and re-run.")
        raise SystemExit(1)

    info("Next: [bold]llmport dev up[/bold] to run the services against the new code.")
