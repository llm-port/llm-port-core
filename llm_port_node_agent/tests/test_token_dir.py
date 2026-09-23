"""Where a user-service agent keeps its Ray cluster token.

The fallback was a fixed /tmp/llm-port/ray shared by every user, and it was
judged usable because mkdir(exist_ok=True) did not raise. On a DGX node that
directory already existed, root-owned, left by Docker; a user-service agent
chose it, and starting the Ray head failed on "Permission denied".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from llm_port_node_agent.ray import manager


def test_uses_the_preferred_directory_when_it_is_writable(tmp_path: Path) -> None:
    preferred = tmp_path / "var-run" / "ray"
    assert manager._writable_token_dir(preferred) == preferred


def test_skips_a_directory_that_exists_but_is_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case that failed: present, so mkdir succeeds, but someone else's."""
    stale = tmp_path / "root-owned"
    stale.mkdir()
    runtime = tmp_path / "run-user"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    real_access = os.access
    monkeypatch.setattr(
        manager.os,
        "access",
        lambda path, mode: False if Path(path) == stale else real_access(path, mode),
    )

    chosen = manager._writable_token_dir(stale)

    assert chosen == runtime / "llmport-agent" / "ray"
    assert chosen.is_dir()


def test_never_falls_back_to_a_directory_shared_between_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(manager.Path, "home", lambda: tmp_path / "home")
    unwritable = tmp_path / "nope"
    monkeypatch.setattr(
        manager.os, "access", lambda path, mode: Path(path) != unwritable
    )

    chosen = manager._writable_token_dir(unwritable)

    assert chosen == tmp_path / "home" / ".local" / "share" / "llmport-agent" / "ray"
    assert "/tmp/llm-port/ray" not in str(chosen).replace("\\\\", "/")
