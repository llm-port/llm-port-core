"""Reclaiming the terminals that host the dev services.

Stopping a service's leaf process is not enough. ``dev up`` starts each one
inside a shell tab -- ``pwsh -NoExit -Command uv run -m llm_port_backend`` --
and that host inherits the listening socket. Kill only the leaf and the port
stays bound by a process that no longer exists, the replacement cannot bind,
it exits without a word, and the previous code keeps answering.

That is what "the backend is serving stale code" was, and it cost hours
before anyone looked at who owned the socket: seven LISTEN entries on port
8000, every one owned by a dead pid.
"""

from __future__ import annotations

from pathlib import Path

import psutil
import pytest

from llmport.commands.dev import dev_up


class _FakeProc:
    def __init__(self, pid: int, cmdline: list[str], cwd: str) -> None:
        self.pid = pid
        self.info = {"cmdline": cmdline}
        self._cwd = cwd
        self.killed = False

    def cwd(self) -> str:
        return self._cwd

    def kill(self) -> None:
        self.killed = True


def _patch(monkeypatch: pytest.MonkeyPatch, procs: list[_FakeProc]) -> None:
    monkeypatch.setattr(dev_up.psutil, "process_iter", lambda _a: list(procs))
    monkeypatch.setattr(dev_up, "_own_process_chain", lambda: set())


def test_stops_the_shell_hosting_a_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The case that kept the socket alive."""
    host = _FakeProc(
        900,
        ["pwsh.EXE", "-NoExit", "-Command", "uv run -m llm_port_backend"],
        str(tmp_path / "llm_port_backend"),
    )
    _patch(monkeypatch, [host])

    assert dev_up._stop_service_hosts(tmp_path) == [900]
    assert host.killed is True


@pytest.mark.parametrize(
    "command",
    [
        "uv run -m llm_port_backend",
        "uv run -m llm_port_api",
        "uv run taskiq worker llm_port_backend.tkq:broker",
        "npm run dev",
    ],
)
def test_recognises_every_service_dev_up_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    host = _FakeProc(1, ["pwsh.EXE", "-NoExit", "-Command", command], str(tmp_path))
    _patch(monkeypatch, [host])

    assert dev_up._stop_service_hosts(tmp_path) == [1]


def test_leaves_a_second_checkout_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same module, different working directory: not ours to stop."""
    other = tmp_path.parent / "another-checkout"
    other.mkdir(exist_ok=True)
    host = _FakeProc(
        2, ["pwsh.EXE", "-NoExit", "-Command", "uv run -m llm_port_backend"], str(other)
    )
    _patch(monkeypatch, [host])

    assert dev_up._stop_service_hosts(tmp_path) == []
    assert host.killed is False


def test_ignores_a_shell_that_merely_mentions_a_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ad-hoc command naming the package is not a running service.

    Matching a bare module name would have made this function kill the very
    terminal driving it.
    """
    procs = [
        _FakeProc(3, ["bash", "-c", "grep -rn x llm_port_backend/"], str(tmp_path)),
        _FakeProc(4, ["python", "-c", "import llm_port_backend"], str(tmp_path)),
    ]
    _patch(monkeypatch, procs)

    assert dev_up._stop_service_hosts(tmp_path) == []
    assert all(not p.killed for p in procs)


def test_never_stops_its_own_process_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host = _FakeProc(
        5, ["pwsh.EXE", "-NoExit", "-Command", "uv run -m llm_port_backend"], str(tmp_path)
    )
    monkeypatch.setattr(dev_up.psutil, "process_iter", lambda _a: [host])
    monkeypatch.setattr(dev_up, "_own_process_chain", lambda: {5})

    assert dev_up._stop_service_hosts(tmp_path) == []
    assert host.killed is False


def test_a_process_that_denies_inspection_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Denied(_FakeProc):
        def cwd(self) -> str:
            raise psutil.AccessDenied(self.pid)

    denied = _Denied(
        6, ["pwsh.EXE", "-NoExit", "-Command", "uv run -m llm_port_backend"], "?"
    )
    _patch(monkeypatch, [denied])

    assert dev_up._stop_service_hosts(tmp_path) == []


def test_matches_on_the_workspace_in_the_command_line_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A host started elsewhere but naming this workspace still counts."""
    host = _FakeProc(
        7,
        ["pwsh.EXE", "-NoExit", "-Command", f"cd {tmp_path}; uv run -m llm_port_api"],
        "C:\\",
    )
    _patch(monkeypatch, [host])

    assert dev_up._stop_service_hosts(tmp_path) == [7]


class TestIsWithin:
    def test_a_subdirectory_is_within(self, tmp_path: Path) -> None:
        assert dev_up._is_within(tmp_path / "a" / "b", tmp_path) is True

    def test_the_root_itself_is_within(self, tmp_path: Path) -> None:
        assert dev_up._is_within(tmp_path, tmp_path) is True

    def test_a_sibling_is_not(self, tmp_path: Path) -> None:
        assert dev_up._is_within(tmp_path.parent / "elsewhere", tmp_path) is False
