"""Tests for ``dev up`` reclaiming the workspace before it starts anything.

Root cause these pin: starting a service whose port is already held does not
fail in any visible way. The new process exits, the old one keeps serving,
and ``dev up`` reports success -- so the running system silently ignores
every change made since the old process started. Hours can go into debugging
code that is already correct.

Stopping first makes that impossible; checking the ports afterwards catches
the case the stop could not fix, which is a process this workspace did not
start.
"""

from __future__ import annotations

import socket
from contextlib import closing
from pathlib import Path

import pytest

from llmport.commands.dev import dev_up


def test_reclaim_reports_what_it_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dev_up, "_stop_workspace_services", lambda _ws: [111, 222])
    monkeypatch.setattr(dev_up, "_ports_still_held", lambda: {})

    dev_up._reclaim_workspace(tmp_path)

    assert "2 process(es)" in capsys.readouterr().out


def test_a_port_we_could_not_free_is_a_warning_not_silence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dangerous case: something else holds the port.

    ``dev up`` cannot stop it -- it belongs to another checkout or was started
    by hand -- so the least it can do is say the service about to start will
    not be the one serving.
    """
    monkeypatch.setattr(dev_up, "_stop_workspace_services", lambda _ws: [])
    monkeypatch.setattr(dev_up, "_ports_still_held", lambda: {8000: "Backend"})
    monkeypatch.setattr(dev_up.time, "sleep", lambda _s: None)

    dev_up._reclaim_workspace(tmp_path)

    # The console wraps to the terminal width, so a phrase can land across
    # two lines; compare on collapsed whitespace rather than the raw output.
    out = " ".join(capsys.readouterr().out.split())
    assert "8000" in out
    assert "exit quietly" in out, "the operator is not warned about the silent failure"


def test_a_port_that_frees_shortly_after_is_not_warned_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A socket outlives the process holding it by a moment."""
    answers = [{8000: "Backend"}, {8000: "Backend"}, {}]
    monkeypatch.setattr(dev_up, "_stop_workspace_services", lambda _ws: [42])
    monkeypatch.setattr(dev_up, "_ports_still_held", lambda: answers.pop(0))
    monkeypatch.setattr(dev_up.time, "sleep", lambda _s: None)

    dev_up._reclaim_workspace(tmp_path)

    assert "still in use" not in capsys.readouterr().out


def test_ports_still_held_sees_a_real_listener() -> None:
    """The probe has to detect an actual socket, not just return {}."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        # Point the probe at the port we just opened.
        original = dict(dev_up._DEV_PORTS)
        dev_up._DEV_PORTS.clear()
        dev_up._DEV_PORTS[port] = "Test"
        try:
            assert dev_up._ports_still_held() == {port: "Test"}
        finally:
            dev_up._DEV_PORTS.clear()
            dev_up._DEV_PORTS.update(original)


def test_reclaim_never_stops_the_process_running_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``dev up`` matching its own command line must not kill itself.

    Its command line contains the workspace path, so it matches the same
    filter every service does.
    """
    import os

    killed: list[int] = []
    monkeypatch.setattr(dev_up.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        dev_up.subprocess,
        "run",
        lambda *_a, **_k: type("R", (), {"stdout": f"{os.getpid()}\n4242\n"})(),
    )
    monkeypatch.setattr(dev_up.os, "kill", lambda pid, _sig: killed.append(pid))

    stopped = dev_up._stop_workspace_services(tmp_path)

    assert os.getpid() not in stopped
    assert killed == [4242]


def test_reclaim_spares_the_shell_that_launched_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression: ``dev up`` killed its own terminal mid-reclaim.

    It runs as ``uv run -m llmport``, so ``uv.exe`` is its parent and that
    parent's command line names the workspace exactly as a service's does.
    Sparing only our own pid left the parent to be killed, which took the
    terminal down with nothing started and no explanation printed.
    """
    import os

    killed: list[int] = []
    parent, grandparent = 900, 901
    chain = {os.getpid(): parent, parent: grandparent, grandparent: 0}

    monkeypatch.setattr(dev_up.platform, "system", lambda: "Linux")
    monkeypatch.setattr(dev_up, "_parent_pid", lambda pid: chain.get(pid) or None)
    monkeypatch.setattr(
        dev_up.subprocess,
        "run",
        lambda *_a, **_k: type(
            "R", (), {"stdout": f"{os.getpid()}\n{parent}\n{grandparent}\n7777\n"}
        )(),
    )
    monkeypatch.setattr(dev_up.os, "kill", lambda pid, _sig: killed.append(pid))

    stopped = dev_up._stop_workspace_services(tmp_path)

    assert killed == [7777], "an ancestor of dev up was killed"
    assert parent not in stopped and grandparent not in stopped


def test_the_chain_ends_even_if_parents_loop(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pid reported as its own ancestor must not hang the walk."""
    import os

    monkeypatch.setattr(dev_up, "_parent_pid", lambda _pid: os.getpid())
    assert dev_up._own_process_chain() == {os.getpid()}
