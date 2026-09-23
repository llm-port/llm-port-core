"""Running the agent as a service without root.

On both DGX nodes sudo needs a password. A non-interactive install therefore
installed the binary and then died writing the system unit, leaving an agent
that only ran while somebody's shell stayed open -- the node went "offline"
the moment the session ended. ``systemd --user`` plus linger needs no
privilege at all, and on DGX OS a user may enable linger for themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llm_port_node_agent import __main__ as agent_main


class TestChoosingTheScope:
    def test_an_explicit_request_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_main, "_passwordless_sudo", lambda: True)
        assert agent_main._choose_user_scope(True) is True
        assert agent_main._choose_user_scope(False) is False

    def test_root_to_hand_means_a_system_unit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_main, "_IS_WINDOWS", False)
        monkeypatch.setattr(agent_main, "_passwordless_sudo", lambda: True)
        assert agent_main._choose_user_scope(None) is False

    def test_nobody_to_type_a_password_means_a_user_unit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case that failed outright before."""
        monkeypatch.setattr(agent_main, "_IS_WINDOWS", False)
        monkeypatch.setattr(agent_main, "_passwordless_sudo", lambda: False)
        monkeypatch.setattr(agent_main, "_user_services_available", lambda: True)
        monkeypatch.setattr(agent_main.sys.stdin, "isatty", lambda: False, raising=False)
        assert agent_main._choose_user_scope(None) is True

    def test_someone_at_the_terminal_can_still_choose_sudo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent_main, "_IS_WINDOWS", False)
        monkeypatch.setattr(agent_main, "_passwordless_sudo", lambda: False)
        monkeypatch.setattr(agent_main, "_user_services_available", lambda: True)
        monkeypatch.setattr(agent_main.sys.stdin, "isatty", lambda: True, raising=False)
        assert agent_main._choose_user_scope(None) is False

    def test_no_user_manager_means_no_user_unit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_main, "_IS_WINDOWS", False)
        monkeypatch.setattr(agent_main, "_passwordless_sudo", lambda: False)
        monkeypatch.setattr(agent_main, "_user_services_available", lambda: False)
        monkeypatch.setattr(agent_main.sys.stdin, "isatty", lambda: False, raising=False)
        assert agent_main._choose_user_scope(None) is False


def test_the_user_unit_runs_the_agent_and_restarts_it(tmp_path: Path) -> None:
    unit = agent_main._build_user_service_content(
        "/home/sachi/.local/bin/llmport-agent", tmp_path / "agent.env"
    )
    assert "ExecStart=/home/sachi/.local/bin/llmport-agent run" in unit
    assert f"EnvironmentFile={tmp_path / 'agent.env'}" in unit
    assert "Restart=always" in unit
    # default.target is the user manager's; multi-user.target does not exist there.
    assert "WantedBy=default.target" in unit


class TestStopping:
    def _record(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        issued: list[list[str]] = []
        monkeypatch.setattr(agent_main, "_require_linux", lambda: None)
        monkeypatch.setattr(agent_main, "_stop_stray_agents", lambda: 0)
        monkeypatch.setattr(agent_main, "_run_cmd", lambda cmd, **_k: issued.append(cmd) or 0)
        monkeypatch.setattr(agent_main, "_sudo_prefix", lambda: ["sudo"])
        return issued

    def test_a_user_service_is_stopped_without_sudo(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        issued = self._record(monkeypatch)
        unit = tmp_path / "llmport-agent.service"
        unit.write_text("[Service]\n")
        monkeypatch.setattr(agent_main, "_user_unit_path", lambda: unit)
        monkeypatch.setattr(agent_main, "_system_unit_path", lambda: tmp_path / "absent.service")

        agent_main._cmd_stop_linux()

        assert ["systemctl", "--user", "disable", "--now", "llmport-agent"] in issued
        assert not any(cmd[0] == "sudo" for cmd in issued), "no system unit, no password"
        assert not unit.exists()

    def test_a_system_service_still_asks_for_what_it_needs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        issued = self._record(monkeypatch)
        system_unit = tmp_path / "system.service"
        system_unit.write_text("[Service]\n")
        monkeypatch.setattr(agent_main, "_user_unit_path", lambda: tmp_path / "absent.service")
        monkeypatch.setattr(agent_main, "_system_unit_path", lambda: system_unit)

        agent_main._cmd_stop_linux()

        assert ["sudo", "systemctl", "disable", "--now", "llmport-agent"] in issued


def test_start_installs_a_user_unit_and_asks_for_linger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    issued: list[list[str]] = []
    monkeypatch.setattr(agent_main, "_require_linux", lambda: None)
    monkeypatch.setattr(agent_main, "_run_cmd", lambda cmd, **_k: issued.append(cmd) or 0)
    monkeypatch.setattr(agent_main, "_USER_UNIT_DIR", tmp_path / "units")
    monkeypatch.setattr(agent_main, "_LINUX_USER_ENV_FILE", tmp_path / "cfg" / "agent.env")
    monkeypatch.setattr(agent_main.getpass, "getuser", lambda: "sachi")

    agent_main._cmd_start_linux_user("/opt/llmport-agent", ["LLM_PORT_NODE_AGENT_BACKEND_URL=http://x"])

    assert (tmp_path / "units" / "llmport-agent.service").is_file()
    assert "BACKEND_URL=http://x" in (tmp_path / "cfg" / "agent.env").read_text()
    assert ["systemctl", "--user", "enable", "llmport-agent"] in issued
    # An upgrade must run the new build: "enable --now" leaves a running
    # service on the old one.
    assert ["systemctl", "--user", "restart", "llmport-agent"] in issued
    assert ["loginctl", "enable-linger", "sachi"] in issued
    assert not any(cmd[0] == "sudo" for cmd in issued)
