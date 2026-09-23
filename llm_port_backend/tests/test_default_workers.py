"""One uvicorn worker by default on Windows.

Workers there share the supervisor's listening socket, and some connections
are never accepted by any of them: measured on the dev workstation, bursts of
eight requests to a four-worker gateway lost two to seven of them outright,
their server side still owned by the supervisor 60 s later.
"""

from __future__ import annotations

import pytest

from llm_port_backend import settings as settings_module


def test_windows_runs_one_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.sys, "platform", "win32")
    assert settings_module._default_workers() == 1


@pytest.mark.parametrize("cpus, expected", [(1, 1), (2, 2), (16, 4)])
def test_elsewhere_it_follows_the_cpus(
    monkeypatch: pytest.MonkeyPatch, cpus: int, expected: int
) -> None:
    monkeypatch.setattr(settings_module.sys, "platform", "linux")
    monkeypatch.setattr(settings_module, "_CPU_COUNT", cpus)
    assert settings_module._default_workers() == expected
