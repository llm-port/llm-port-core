"""A join whose installer run died can be picked up by the next run.

Found in an end-to-end run on the workstation node: the first installer run
was stopped while it waited for approval, and running the install line again
said "that run will pick up the credential" and exited -- with no run left to
pick it up. The backend rightly hands the poll secret out only once, so the
agent keeps it on disk and a later run resumes the wait.

Also here: the frozen binary's children get the machine's own libraries. In
the same run `systemctl` loaded the bundle's older libcrypto and failed right
after the join was approved, so the agent never started.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from llm_port_node_agent import __main__ as agent_main
from llm_port_node_agent import backend_client, gpu, preflight, runtimes
from llm_port_node_agent.state_store import StateStore


class _Backend:
    """Scripted join answers; records what each collect was asked with."""

    def __init__(self, asked: dict[str, Any], collects: list[Any]) -> None:
        self.asked = asked
        self.collects = collects
        self.collected_with: list[str] = []

    def __call__(self, _config: Any) -> _Backend:
        return self

    async def request_join(self, **_: Any) -> dict[str, Any]:
        return dict(self.asked)

    async def collect_join(self, *, request_id: str, poll_secret: str) -> dict[str, Any]:
        self.collected_with.append(poll_secret)
        answer = self.collects.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def close(self) -> None:
        return None


@pytest.fixture()
def config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        state_path=tmp_path / "state.json",
        backend_url="http://10.88.10.220:8100",
        container_runtime=None,
        model_store_root="/srv/llm-port/models",
        ray_session_dir="/var/lib/llm-port/ray",
        agent_id="workstation-wsl",
        advertise_host="10.88.10.220",
    )


@pytest.fixture(autouse=True)
def quiet_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def capabilities(*_: Any, **__: Any) -> dict[str, Any]:
        return {"gpu_count": 1}

    monkeypatch.setattr(runtimes, "detect_runtime", lambda preferred=None: None)
    monkeypatch.setattr(gpu, "detect_gpu", lambda: None)
    monkeypatch.setattr(preflight, "build_static_capabilities", capabilities)
    monkeypatch.setattr(agent_main, "_JOIN_POLL_SECONDS", 0)


def _use(monkeypatch: pytest.MonkeyPatch, backend: _Backend) -> None:
    monkeypatch.setattr(backend_client, "BackendClient", backend)


@pytest.mark.anyio()
async def test_a_run_stopped_while_waiting_is_resumed_by_the_next(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    first = _Backend({"id": "r1", "code": "9CX-4MM", "poll_secret": "s1"}, [asyncio.CancelledError()])
    _use(monkeypatch, first)
    with pytest.raises(asyncio.CancelledError):  # Ctrl+C, a dropped ssh session
        await agent_main._join_flow(config)
    assert StateStore(config.state_path).state.pending_join == {
        "id": "r1", "backend_url": config.backend_url, "poll_secret": "s1",
    }

    # The backend answers a second ask with the same request and no secret.
    approved = {"status": "approved", "credential": "cred", "node_id": "n1", "agent_id": "workstation-wsl"}
    second = _Backend({"id": "r1", "code": "9CX-4MM", "poll_secret": None, "already_pending": True}, [approved])
    _use(monkeypatch, second)
    assert await agent_main._join_flow(config) is True

    assert second.collected_with == ["s1"], "the kept secret collects the credential"
    state = StateStore(config.state_path).state
    assert (state.credential, state.node_id, state.pending_join) == ("cred", "n1", None)
    assert "earlier run" in capsys.readouterr().out


@pytest.mark.anyio()
async def test_a_request_made_elsewhere_is_explained_not_waited_on(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    """No secret on disk for that request: say how to get unstuck."""
    backend = _Backend({"id": "r9", "code": "K7M-3QP", "poll_secret": None, "already_pending": True}, [])
    _use(monkeypatch, backend)
    assert await agent_main._join_flow(config) is False
    assert backend.collected_with == []
    out = capsys.readouterr().out
    assert "K7M-3QP" in out
    assert "Not this one" in out, "the way out when the other run is gone"


@pytest.mark.anyio()
async def test_a_refused_request_forgets_its_secret(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace,
) -> None:
    _use(monkeypatch, _Backend({"id": "r2", "code": "6G3-TTR", "poll_secret": "s2"}, [{"status": "rejected"}]))
    assert await agent_main._join_flow(config) is False
    assert StateStore(config.state_path).state.pending_join is None


@pytest.mark.anyio()
async def test_a_secret_for_another_backend_is_not_used(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace,
) -> None:
    store = StateStore(config.state_path)
    store.state.pending_join = {"id": "r1", "backend_url": "http://elsewhere:8000", "poll_secret": "old"}
    store.save()
    backend = _Backend({"id": "r1", "code": "9CX-4MM", "poll_secret": None}, [])
    _use(monkeypatch, backend)
    assert await agent_main._join_flow(config) is False
    assert backend.collected_with == []


def test_children_of_the_frozen_binary_get_the_machines_libraries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI12345")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/vendor/lib")
    agent_main._restore_system_library_path()
    assert os.environ["LD_LIBRARY_PATH"] == "/opt/vendor/lib"

    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI12345")
    agent_main._restore_system_library_path()
    assert "LD_LIBRARY_PATH" not in os.environ, "there was none before the launcher set it"


def test_a_source_install_leaves_the_library_path_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/local/cuda/lib64")
    agent_main._restore_system_library_path()
    assert os.environ["LD_LIBRARY_PATH"] == "/usr/local/cuda/lib64"
