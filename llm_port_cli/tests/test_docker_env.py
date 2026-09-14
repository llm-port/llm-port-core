"""Tests for the Docker Desktop / Rancher Desktop endpoint fallback.

Root cause this pins: on machines that had Docker Desktop installed first,
the docker CLI's default context points at
``npipe:////./pipe/dockerDesktopLinuxEngine``.  When Docker Desktop is down
but Rancher Desktop is running, that pipe does not exist and every
``docker`` call fails — even though Rancher Desktop's daemon answers on
``npipe:////./pipe/docker_engine``.  :func:`ensure_docker_host` detects the
dead endpoint and re-exports ``DOCKER_HOST`` so every child docker
subprocess reaches the live daemon.
"""

from __future__ import annotations

import os

import pytest

from llmport.core import docker_env

# On macOS/Linux the Rancher Desktop fallback list has exactly these two.
_NON_WIN_ENDPOINTS = 2


@pytest.fixture(autouse=True)
def _clean_docker_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test without a preset DOCKER_HOST unless the test sets one."""
    monkeypatch.delenv("DOCKER_HOST", raising=False)


@pytest.fixture(autouse=True)
def _docker_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a fake docker binary on PATH by default.

    Independent of :func:`_clean_docker_host` (patches the binary lookup vs.
    the environment), so the two autouse fixtures do not interact.
    """
    monkeypatch.setattr(
        docker_env.shutil, "which", lambda name: r"C:\docker\docker.exe" if name == "docker" else None
    )


class _ProbeFake:
    """Records probe calls and answers per-endpoint."""

    def __init__(self, answering: set[str] | None) -> None:
        self.answering = set(answering or ())
        self.calls: list[str] = []

    def __call__(self, endpoint: str) -> bool:
        self.calls.append(endpoint)
        return endpoint in self.answering


def test_no_docker_binary_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_env.shutil, "which", lambda name: None)
    assert docker_env.resolve_docker_host() is None
    assert docker_env.ensure_docker_host() is None


def test_current_endpoint_alive_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _ProbeFake({"default"})
    monkeypatch.setattr(docker_env, "_probe", fake)
    assert docker_env.ensure_docker_host() == "default"
    assert "DOCKER_HOST" not in os.environ
    assert fake.calls == ["default"]


def test_dead_desktop_endpoint_falls_back_to_rancher_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_env, "is_windows", lambda: True)
    fake = _ProbeFake({docker_env.rancher_endpoints()[0]})
    monkeypatch.setattr(docker_env, "_probe", fake)
    monkeypatch.setenv("DOCKER_HOST", "npipe:////./pipe/dockerDesktopLinuxEngine")

    host = docker_env.ensure_docker_host()

    assert host == docker_env.rancher_endpoints()[0]
    assert os.environ["DOCKER_HOST"] == docker_env.rancher_endpoints()[0]
    # The dead context is probed first, then the Rancher endpoint.
    assert fake.calls == ["npipe:////./pipe/dockerDesktopLinuxEngine", docker_env.rancher_endpoints()[0]]
    # (A short informational notice is also printed, but Rich's console
    # output is not reliably capturable under pytest, so it is not asserted.)


def test_no_endpoint_alive_export_left_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_env, "_probe", _ProbeFake(None))
    monkeypatch.setenv("DOCKER_HOST", "npipe:////./pipe/dockerDesktopLinuxEngine")
    assert docker_env.ensure_docker_host() is None
    assert os.environ["DOCKER_HOST"] == "npipe:////./pipe/dockerDesktopLinuxEngine"


def test_rancher_endpoints_are_platform_specific() -> None:
    endpoints = docker_env.rancher_endpoints()
    if docker_env.is_windows():
        assert endpoints == ["npipe:////./pipe/docker_engine"]
    else:
        # app-local socket + the conventional /var/run/docker.sock
        assert len(endpoints) == _NON_WIN_ENDPOINTS and all(e.startswith("unix://") for e in endpoints)
