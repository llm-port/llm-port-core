"""Stopping an agent that no service manager knows about.

``llmport-agent stop`` used to run ``systemctl disable --now`` and consider
the job done. An agent started by hand -- ``llmport-agent run``, which is
what the source install in the onboarding docs leaves behind -- is not a
unit, so it survived the uninstall untouched.

Observed on a live node: the binary was gone from PATH, the unit was gone,
the config file was gone, and the agent was still streaming to the backend
twelve hours later, keeping the node green in the UI. Deleting its config
changed nothing, because it had read that at startup.
"""

from __future__ import annotations

import os

import psutil
import pytest

from llm_port_node_agent import __main__ as agent_main


class _FakeProc:
    """Enough of psutil.Process for the matching logic."""

    def __init__(self, pid: int, cmdline: list[str] | None) -> None:
        self.pid = pid
        self.info = {"cmdline": cmdline}
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def _patch_scan(monkeypatch: pytest.MonkeyPatch, procs: list[_FakeProc]) -> None:
    monkeypatch.setattr(agent_main.psutil, "process_iter", lambda _attrs: list(procs))


class TestMatching:
    def test_finds_a_frozen_binary_running_in_the_foreground(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_scan(monkeypatch, [_FakeProc(4242, ["/usr/local/bin/llmport-agent", "run"])])
        monkeypatch.setattr(agent_main, "_own_process_chain", lambda: {os.getpid()})

        assert [p.pid for p in agent_main._running_agents()] == [4242]

    def test_finds_a_source_install_running_under_python(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case that actually escaped.

        On Linux systemctl never knew about it; on Windows taskkill with
        ``/IM llmport-agent.exe`` cannot see it either, because the image
        name is python.
        """
        cmdline = [
            "/home/sachi/.local/llmport-agent-venv/bin/python3",
            "/home/sachi/.local/llmport-agent-venv/bin/llmport-agent",
            "run",
        ]
        _patch_scan(monkeypatch, [_FakeProc(1782905, cmdline)])
        monkeypatch.setattr(agent_main, "_own_process_chain", lambda: {os.getpid()})

        assert [p.pid for p in agent_main._running_agents()] == [1782905]

    def test_never_matches_a_concurrent_stop_invocation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stop must not kill itself: its own argv says llmport-agent too."""
        _patch_scan(monkeypatch, [_FakeProc(5, ["/usr/local/bin/llmport-agent", "stop"])])
        monkeypatch.setattr(agent_main, "_own_process_chain", lambda: {os.getpid()})

        assert agent_main._running_agents() == []

    def test_spares_this_process_and_every_ancestor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lesson from killing a parent shell once already.

        An ancestor command line can carry the pattern we match on, and
        killing an ancestor takes this process down with it.
        """
        parent, grandparent = 111, 222
        procs = [
            _FakeProc(parent, ["sh", "-c", "llmport-agent run"]),
            _FakeProc(grandparent, ["/usr/local/bin/llmport-agent", "run"]),
            _FakeProc(999, ["/usr/local/bin/llmport-agent", "run"]),
        ]
        _patch_scan(monkeypatch, procs)
        monkeypatch.setattr(
            agent_main, "_own_process_chain", lambda: {os.getpid(), parent, grandparent}
        )

        assert [p.pid for p in agent_main._running_agents()] == [999]

    def test_ignores_processes_that_merely_mention_the_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        procs = [
            _FakeProc(7, ["tail", "-f", "/var/log/llmport-agent.log"]),
            _FakeProc(8, ["vim", "llmport-agent.spec"]),
            _FakeProc(9, None),
        ]
        _patch_scan(monkeypatch, procs)
        monkeypatch.setattr(agent_main, "_own_process_chain", lambda: {os.getpid()})

        assert agent_main._running_agents() == []

    def test_the_real_chain_always_contains_this_process(self) -> None:
        """Unpatched, against the real process tree."""
        assert os.getpid() in agent_main._own_process_chain()


class TestStopping:
    def test_terminates_politely_before_killing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SIGTERM first, so the agent closes its backend session cleanly."""
        stray = _FakeProc(4242, ["/usr/local/bin/llmport-agent", "run"])
        calls = iter([[stray], []])
        monkeypatch.setattr(agent_main, "_running_agents", lambda: next(calls))
        monkeypatch.setattr(agent_main.psutil, "wait_procs", lambda procs, timeout: (procs, []))

        assert agent_main._stop_stray_agents() == 1
        assert stray.terminated is True
        assert stray.killed is False

    def test_escalates_to_kill_when_it_will_not_go(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stray = _FakeProc(4242, ["/usr/local/bin/llmport-agent", "run"])
        calls = iter([[stray], []])
        monkeypatch.setattr(agent_main, "_running_agents", lambda: next(calls))
        monkeypatch.setattr(agent_main.psutil, "wait_procs", lambda procs, timeout: ([], procs))

        assert agent_main._stop_stray_agents() == 1
        assert stray.killed is True

    def test_says_so_when_one_survives(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A stop that did not stop must not be reported as success.

        Silence here is what let a node keep reporting healthy after it had
        supposedly been removed.
        """
        stray = _FakeProc(4242, ["/usr/local/bin/llmport-agent", "run"])
        monkeypatch.setattr(agent_main, "_running_agents", lambda: [stray])
        monkeypatch.setattr(agent_main.psutil, "wait_procs", lambda procs, timeout: ([], procs))

        assert agent_main._stop_stray_agents() == 0
        assert "could not stop agent process(es) [4242]" in capsys.readouterr().err

    def test_does_nothing_and_says_nothing_when_the_host_is_clean(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(agent_main, "_running_agents", lambda: [])

        assert agent_main._stop_stray_agents() == 0
        assert capsys.readouterr().out == ""

    def test_asks_for_privilege_rather_than_reporting_a_stop_it_did_not_make(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent running as a service account is not ours to signal."""

        class _Denied(_FakeProc):
            def terminate(self) -> None:
                raise psutil.AccessDenied(self.pid)

        stray = _Denied(4242, ["/usr/local/bin/llmport-agent", "run"])
        issued: list[list[str]] = []
        monkeypatch.setattr(agent_main, "_IS_WINDOWS", False)
        monkeypatch.setattr(agent_main, "_sudo_prefix", lambda: ["sudo"])
        monkeypatch.setattr(
            agent_main, "_run_cmd", lambda cmd, **kw: issued.append(cmd) or 0
        )

        agent_main._signal_agent(stray, "terminate")

        assert issued == [["sudo", "kill", "-TERM", "4242"]]

    def test_a_process_that_exits_first_is_not_an_error(self) -> None:
        class _Gone(_FakeProc):
            def terminate(self) -> None:
                raise psutil.NoSuchProcess(self.pid)

        agent_main._signal_agent(_Gone(4242, ["llmport-agent", "run"]), "terminate")


class TestCommandLineShape:
    """What counts as running the agent, versus mentioning it.

    psutil reports real argv on Linux but re-splits the raw command line on
    Windows, so a long shell invocation arrives as many tokens. Requiring the
    agent token and ``run`` to be adjacent, and excluding shells outright, is
    what separates the two.
    """

    @pytest.mark.parametrize(
        ("cmdline", "expected", "why"),
        [
            (["/usr/local/bin/llmport-agent", "run"], True, "frozen binary"),
            (
                ["/h/venv/bin/python3", "/h/venv/bin/llmport-agent", "run"],
                True,
                "source install",
            ),
            (["uv", "run", "llmport-agent", "run"], True, "wrapper still found"),
            (["python", "/x/bin/llmport-agent", "run", "--verbose"], True, "with flags"),
            (["/usr/local/bin/llmport-agent", "stop"], False, "a stop invocation"),
            (["/usr/local/bin/llmport-agent"], False, "no subcommand"),
            (["tail", "-f", "/var/log/llmport-agent.log"], False, "mentions the log"),
            (["vim", "llmport-agent.spec"], False, "editing the spec"),
            (
                ["bash", "-c", "nohup", "/usr/local/bin/llmport-agent", "run", "&"],
                False,
                "a shell launching it is not it",
            ),
            (["cmd.exe", "/c", "/x/llmport-agent", "run"], False, "windows shell"),
            (
                ["bash", "-c", "cd", "/x/llmport-agent", "&&", "./thing", "run"],
                False,
                "split shell blob, no adjacency",
            ),
            ([], False, "empty"),
        ],
    )
    def test_shape(self, cmdline: list[str], expected: bool, why: str) -> None:
        assert agent_main._is_agent_cmdline(cmdline) is expected, why
