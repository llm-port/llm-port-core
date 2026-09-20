"""Tests for local-checkout detection.

The behaviour under test is that ``dev up`` and ``dev init`` no longer
require a previous ``dev init`` to have written config: they find a
checkout the developer already has. The fallbacks matter as much as the
happy path, because getting them wrong turns a working setup into a second
clone beside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmport.commands.dev.dev_init import _resolve_init_workspace
from llmport.core.workspace import (
    WORKSPACE_MARKERS,
    detect_workspace,
    find_service_dir,
    is_workspace,
)


def _make_workspace(root: Path, *, monorepo: bool) -> Path:
    """Create the marker directories in one of the two supported layouts."""
    base = root / "llm-port-core" if monorepo else root
    for name in WORKSPACE_MARKERS:
        (base / name).mkdir(parents=True)
    return root


@pytest.mark.parametrize("monorepo", [True, False], ids=["monorepo", "flat"])
def test_is_workspace_accepts_both_layouts(tmp_path: Path, monorepo: bool) -> None:
    """``dev init`` clones a monorepo; older/manual checkouts are flat."""
    root = _make_workspace(tmp_path / "ws", monorepo=monorepo)
    assert is_workspace(root)


def test_is_workspace_rejects_a_partial_checkout(tmp_path: Path) -> None:
    """Backend alone is not a workspace: the shared dir holds the compose
    file and the .env everything else is derived from."""
    (tmp_path / "llm_port_backend").mkdir(parents=True)
    assert not is_workspace(tmp_path)


def test_is_workspace_rejects_an_empty_directory(tmp_path: Path) -> None:
    assert not is_workspace(tmp_path)


def test_detects_from_the_workspace_root(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws", monorepo=True)
    assert detect_workspace(root) == root.resolve()


def test_detects_from_inside_a_service(tmp_path: Path) -> None:
    """A developer stands in a service directory, not at the root."""
    root = _make_workspace(tmp_path / "ws", monorepo=True)
    deep = find_service_dir(root, "llm_port_backend") / "src" / "pkg"
    deep.mkdir(parents=True)

    detected = detect_workspace(deep)
    # The monorepo directory is itself a valid flat workspace, and it is the
    # nearer of the two — either root works, so assert the search resolved to
    # one of them rather than pinning the tie-break.
    assert detected in {root.resolve(), (root / "llm-port-core").resolve()}


def test_returns_none_outside_any_checkout(tmp_path: Path) -> None:
    """Callers fall back to their previous behaviour on None, so an
    unrelated directory must not be adopted as a workspace."""
    empty = tmp_path / "somewhere" / "else"
    empty.mkdir(parents=True)
    assert detect_workspace(empty) is None


def test_nearest_workspace_wins(tmp_path: Path) -> None:
    """A checkout nested inside another resolves to itself."""
    outer = _make_workspace(tmp_path / "outer", monorepo=False)
    inner = _make_workspace(outer / "projects" / "inner", monorepo=False)
    assert detect_workspace(inner) == inner.resolve()


# ── dev init adoption ────────────────────────────────────────────


def test_init_adopts_a_detected_checkout(tmp_path: Path, monkeypatch) -> None:
    """The default argument means "here", so an existing checkout wins over
    cloning a second copy beside it."""
    root = _make_workspace(tmp_path / "ws", monorepo=True)
    monkeypatch.chdir(find_service_dir(root, "llm_port_backend"))

    resolved = _resolve_init_workspace(".", explicit=False)
    assert is_workspace(resolved)
    assert resolved != Path.cwd()


def test_init_honours_an_explicit_path(tmp_path: Path, monkeypatch) -> None:
    """An explicit path says where the workspace goes; a checkout in some
    ancestor must not hijack it."""
    root = _make_workspace(tmp_path / "ws", monorepo=True)
    monkeypatch.chdir(root)
    target = tmp_path / "ws" / "somewhere" / "new"

    assert _resolve_init_workspace(str(target), explicit=True) == target.resolve()


def test_init_falls_back_to_the_given_path_outside_a_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    """A genuinely fresh setup is unaffected: nothing to detect, clone as before."""
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.chdir(fresh)

    assert _resolve_init_workspace(".", explicit=False) == fresh.resolve()
