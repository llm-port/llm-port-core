"""Git clone and checkout helpers for dev mode.

Provides functions to clone repositories from the ``llm-port`` GitHub
organisation, with support for HTTPS (default) and SSH clone methods.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from llmport.core.console import console, error, info, success, warning
from llmport.core.registry import CORE_REPO, REPO_DIR_MAP, core_repo_clone_url, repo_clone_url


@dataclass
class CloneResult:
    """Result of a single repo clone operation."""

    repo: str
    local_dir: str
    cloned: bool = False
    skipped: bool = False
    error: str = ""


def clone_repo(
    repo: str,
    *,
    target_dir: Path,
    method: str = "https",
    branch: str | None = None,
    force: bool = False,
    token: str = "",
) -> CloneResult:
    """Clone a single repo into ``target_dir / local_name``.

    Skips if the directory already exists unless *force* is True,
    in which case it runs ``git pull`` to update instead.
    """
    local_name = REPO_DIR_MAP.get(repo, repo)
    dest = target_dir / local_name
    result = CloneResult(repo=repo, local_dir=str(dest))

    if dest.exists():
        if force:
            git = shutil.which("git")
            if not git:
                result.error = "git not found on PATH"
                return result
            try:
                proc = subprocess.run(
                    [git, "pull"],
                    cwd=str(dest),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if proc.returncode == 0:
                    result.cloned = True  # updated
                else:
                    result.error = proc.stderr.strip() or f"git pull exited with code {proc.returncode}"
            except subprocess.TimeoutExpired:
                result.error = "git pull timed out after 120s"
            except OSError as exc:
                result.error = str(exc)
            return result
        else:
            result.skipped = True
            return result

    git = shutil.which("git")
    if not git:
        result.error = "git not found on PATH"
        return result

    url = repo_clone_url(repo, method=method, token=token)
    cmd: list[str] = [git, "clone", url, str(dest)]
    if branch:
        cmd.extend(["--branch", branch])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)  # noqa: S603
        if proc.returncode == 0:
            result.cloned = True
        else:
            result.error = proc.stderr.strip() or f"git clone exited with code {proc.returncode}"
    except subprocess.TimeoutExpired:
        result.error = "git clone timed out after 120s"
    except OSError as exc:
        result.error = str(exc)

    return result


def clone_all_repos(
    repos: list[str],
    *,
    target_dir: Path,
    method: str = "https",
    branch: str | None = None,
    force: bool = False,
    token: str = "",
) -> list[CloneResult]:
    """Clone all repos into the target directory.

    Clones the core monorepo once and all subdirectories are available
    immediately. Falls back to individual repo clones for repos not
    in the monorepo (e.g. EE add-ons).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    results: list[CloneResult] = []

    # Clone the core monorepo first
    with console.status("[bold cyan]Cloning core monorepo…") as _status:
        core_dest = target_dir / CORE_REPO
        core_result = CloneResult(repo=CORE_REPO, local_dir=str(core_dest))

        if core_dest.exists():
            if force:
                git = shutil.which("git")
                if git:
                    try:
                        proc = subprocess.run(
                            [git, "pull"], cwd=str(core_dest),
                            capture_output=True, text=True, timeout=120,
                        )
                        core_result.cloned = proc.returncode == 0
                        if not core_result.cloned:
                            core_result.error = proc.stderr.strip()
                    except (subprocess.TimeoutExpired, OSError) as exc:
                        core_result.error = str(exc)
                else:
                    core_result.error = "git not found on PATH"
            else:
                core_result.skipped = True
        else:
            git = shutil.which("git")
            if not git:
                core_result.error = "git not found on PATH"
            else:
                url = core_repo_clone_url(method=method, token=token)
                cmd: list[str] = [git, "clone", url, str(core_dest)]
                if branch:
                    cmd.extend(["--branch", branch])
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
                    core_result.cloned = proc.returncode == 0
                    if not core_result.cloned:
                        core_result.error = proc.stderr.strip()
                except (subprocess.TimeoutExpired, OSError) as exc:
                    core_result.error = str(exc)

        results.append(core_result)
        if core_result.skipped:
            warning(f"{CORE_REPO} → already exists, skipped")
        elif core_result.cloned:
            success(f"{CORE_REPO} → cloned")
        else:
            error(f"{CORE_REPO} → {core_result.error}")

    # Clone any remaining repos not in the monorepo (e.g. EE repos)
    monorepo_dirs = {r.local_dir for r in __import__("llmport.core.registry", fromlist=["REPOS"]).REPOS}
    extra_repos = [r for r in repos if REPO_DIR_MAP.get(r, r) not in monorepo_dirs]

    if extra_repos:
        with console.status("[bold cyan]Cloning additional repositories…") as _status:
            for repo in extra_repos:
                _status.update(f"Cloning [bold]{repo}[/bold]…")
                res = clone_repo(repo, target_dir=target_dir, method=method, branch=branch, force=force, token=token)
                results.append(res)

                if res.skipped:
                    warning(f"{repo} → already exists, skipped")
                elif res.cloned:
                    success(f"{repo} → cloned")
                else:
                    error(f"{repo} → {res.error}")

    return results


def checkout_branch(repo_dir: Path, branch: str) -> bool:
    """Checkout a branch in an existing repo."""
    git = shutil.which("git")
    if not git:
        return False
    try:
        result = subprocess.run(  # noqa: S603
            [git, "checkout", branch],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def current_branch(repo_dir: Path) -> str:
    """Get the current branch of a repo."""
    git = shutil.which("git")
    if not git:
        return "unknown"
    try:
        result = subprocess.run(  # noqa: S603
            [git, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"
