"""Tests for how ``dev up`` opens terminals on Windows.

Root cause this pins: ``wt new-tab`` opens a *new window* on every
invocation. The name describes what goes in the window, not where the window
comes from, so launching backend, worker, gateway and frontend produced four
Windows Terminal windows spread across the desktop rather than four tabs.

``-w <name>`` is the documented fix -- Terminal creates the window on first
use and adds tabs to it thereafter -- and a name is used rather than ``0``
("most recent window") so the tabs never land in a window the operator is
working in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmport.commands.dev import dev_up


class _Recorder:
    """Stands in for subprocess.Popen, recording argv instead of spawning."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):  # noqa: ANN001, ANN204
        self.calls.append(list(argv))
        return object()


@pytest.fixture()
def windows_with_wt(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """A Windows box with Windows Terminal and PowerShell 7 on PATH."""
    recorder = _Recorder()
    monkeypatch.setattr(dev_up.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        dev_up, "_which", lambda name: f"C:\\fake\\{name}.exe" if name in {"wt", "pwsh"} else None
    )
    monkeypatch.setattr(dev_up.subprocess, "Popen", recorder)
    return recorder


def test_every_service_lands_in_one_named_window(
    windows_with_wt: _Recorder, tmp_path: Path
) -> None:
    """Four services, four tabs, one window."""
    for title in ("Backend", "Worker", "API Gateway", "Frontend"):
        assert dev_up._launch_terminal(title, tmp_path, "echo hi", headless=False)

    assert len(windows_with_wt.calls) == 4
    for argv in windows_with_wt.calls:
        # The window must be named *before* the command, or Terminal treats
        # it as an argument to new-tab rather than as a window target.
        assert argv[1:3] == ["-w", dev_up._WT_WINDOW_NAME], argv
        assert argv[3] == "new-tab", argv

    titles = [argv[argv.index("--title") + 1] for argv in windows_with_wt.calls]
    assert titles == ["Backend", "Worker", "API Gateway", "Frontend"]


def test_the_window_is_named_not_most_recent(
    windows_with_wt: _Recorder, tmp_path: Path
) -> None:
    """``-w 0`` would hijack whatever Terminal window was last touched."""
    dev_up._launch_terminal("Backend", tmp_path, "echo hi", headless=False)
    argv = windows_with_wt.calls[0]
    assert dev_up._WT_WINDOW_NAME not in {"0", "last", "-1", "new"}
    assert "0" not in argv[1:3]


def test_each_tab_still_starts_where_the_service_lives(
    windows_with_wt: _Recorder, tmp_path: Path
) -> None:
    """Sharing a window must not mean sharing a working directory."""
    backend, frontend = tmp_path / "backend", tmp_path / "frontend"
    dev_up._launch_terminal("Backend", backend, "uv run -m llm_port_backend", headless=False)
    dev_up._launch_terminal("Frontend", frontend, "npm run dev", headless=False)

    dirs = [
        argv[argv.index("--startingDirectory") + 1] for argv in windows_with_wt.calls
    ]
    assert dirs == [str(backend), str(frontend)]


def test_without_windows_terminal_it_still_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No ``wt``: fall back to a console window rather than failing.

    A machine with PowerShell but no Windows Terminal is an ordinary Windows
    Server, and ``dev up`` has to work there -- just without tabs.
    """
    recorder = _Recorder()
    monkeypatch.setattr(dev_up.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        dev_up, "_which", lambda name: "C:\\fake\\pwsh.exe" if name == "pwsh" else None
    )
    monkeypatch.setattr(dev_up.subprocess, "Popen", recorder)

    assert dev_up._launch_terminal("Backend", tmp_path, "echo hi", headless=False)
    argv = windows = recorder.calls[0]
    assert "-w" not in argv
    assert "pwsh.exe" in windows[0]
