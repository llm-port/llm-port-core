"""Ray lifecycle facade + bootstrap CLI layer tests (SDK-First refactor).

Covers :class:`RayManager` (dispatcher-facing surface), token delivery
(``_write_token_securely``), Ray 2.58 token-auth env construction
(``RAY_AUTH_MODE`` / ``RAY_AUTH_TOKEN_PATH``, **not**
``--redis-password-file``), the optional dashboard flag, version threading,
``ensure_runtime`` reporting, and best-effort ``stop_ray`` — the baseline 18
Ray tests re-expressed against :class:`RayRuntime` (the only CLI layer) after
removal of the legacy ``process.py`` / ``status.py`` (``ray list nodes``).

Companion file :mod:`test_ray_status` carries the enriched
``get_status`` probe, the §24 mandatory tests (Dashboard-disabled status,
CLI isolation, tier non-gating, typed errors) and the
GET_RAY_SERVE_STATUS dispatch.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_port_node_agent.dispatcher import CommandDispatcher
from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.policy_guard import PolicyGuard
from llm_port_node_agent.runtime_manager import RuntimeManagerError
from llm_port_node_agent.state_store import StateStore
from llm_port_node_agent.ray import errors
from llm_port_node_agent.ray.manager import RayManager
from llm_port_node_agent.ray.runtime import RayRuntime
from llm_port_node_agent.ray.schemas import (
    EnsureRayRuntimePayload,
    JoinRayClusterPayload,
    StartRayHeadPayload,
    StopRayPayload,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal httpx-like response stub for the backend secret endpoint."""

    def __init__(self, json_payload: Any, status_code: int = 200) -> None:
        self._json = json_payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def state(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.json")
    store.state.credential = "cred-id.deadbeef"
    return store


@pytest.fixture()
def ray_manager(state: StateStore, tmp_path: Path) -> RayManager:
    return RayManager(
        state_store=state,
        events=EventBuffer(),
        ray_base_path=str(tmp_path / "ray-base"),
        token_dir=tmp_path / "ray-tokens",
        http=MagicMock(),
    )


@pytest.fixture()
def fake_runtime(ray_manager: RayManager, tmp_path: Path) -> RayRuntime:
    """CLI-backed runtime with a mocked process seam.

    The manager keeps a *real* :class:`RayRuntime` so the public
    ``start_head`` / ``join_cluster`` arg-building is exercised end to end.
    Only the leaf process seam is stubbed: ``_exec`` records ``argv`` +
    ``env`` + ``version`` + ``label`` and never spawns a subprocess.
    ``stop`` is likewise an ``AsyncMock`` because ``stop_ray`` /
    ``leave_cluster`` call it directly and the tests assert on its kwargs.
    ``_core`` is mocked too so lifecycle tests never attach to a live GCS.
    The manager aliases the runtime as both ``_runtime`` and ``_process``
    (historical name kept for compatibility).
    """
    rt = RayRuntime(ray_base_path=str(tmp_path / "ray"))
    # Real argv-building runs; the subprocess spawn is recorded, not spawned.
    rt._exec = AsyncMock(return_value=MagicMock(returncode=0, stdout=b"", stderr=b""))
    rt.stop = AsyncMock(return_value={"stopped": True})
    ray_manager._runtime = rt
    ray_manager._process = rt
    # Lifecycle tests are about the CLI path — keep them off GCS entirely.
    ray_manager._core = MagicMock()
    return rt


def _stub_token_fetch(ray_manager: RayManager) -> None:
    ray_manager._http = MagicMock()
    ray_manager._http.get = AsyncMock(return_value=_FakeResponse({"token": "tok"}))


def _last_exec_args(rt: Any) -> list[str]:
    """The argv recorded in the last ``_exec`` call."""
    return list(rt._exec.call_args.args[0])


def _last_exec_env(rt: Any) -> dict[str, str]:
    return rt._exec.call_args.kwargs.get("env") or {}


async def _noop_progress(*_a: Any, **_kw: Any) -> None:  # pragma: no cover
    return None


# ---------------------------------------------------------------------------
# Dispatcher normalization (Ray branches RISE so handle() reports success=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_dispatcher_ray_without_manager_reports_failure(state: StateStore) -> None:
    """When ray_manager is None, the dispatcher must NOT report success=True."""
    d = CommandDispatcher(
        state_store=state,
        runtime_manager=MagicMock(),
        policy_guard=PolicyGuard(),
        events=EventBuffer(),
        ray_manager=None,
    )
    emit = AsyncMock()
    result = await d.handle(
        {"id": "cmd-none-1", "command_type": "ensure_ray_runtime", "payload": {}},
        emit,
    )
    assert result["success"] is False
    assert result["error_code"] == "runtime_error"


@pytest.mark.asyncio()
async def test_dispatcher_ray_error_is_normalized_not_double_wrapped(state: StateStore) -> None:
    """A RayManager that raises RuntimeManagerError must surface as
    success=False / error_code='runtime_error' — not as success=True."""
    ray = RayManager(
        state_store=state, events=EventBuffer(),
        ray_base_path="/opt/llm-port/ray", token_dir=Path("/tmp/llm-port-ray-test-tok"),
    )

    async def _boom(*_a: Any, **_kw: Any) -> dict:
        raise RuntimeManagerError("boom")

    ray.start_head = _boom  # type: ignore[method-assign]
    d = CommandDispatcher(
        state_store=state, runtime_manager=MagicMock(), policy_guard=PolicyGuard(),
        events=EventBuffer(), ray_manager=ray,
    )
    emit = AsyncMock()
    result = await d.handle(
        {"id": "cmd-ray-2", "command_type": "start_ray_head", "payload": {"credential_ref": "ref-1"}},
        emit,
    )
    assert result["success"] is False
    assert result["error_code"] == "runtime_error"
    assert result["error_message"] == "boom"
    assert "result" not in result or result.get("result") in (None, {})


# ---------------------------------------------------------------------------
# Token fetch: real HTTP call pattern with Bearer node credential
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_write_token_fetches_from_backend(ray_manager: RayManager) -> None:
    ray_manager._http = MagicMock()
    ray_manager._http.get = AsyncMock(return_value=_FakeResponse({"token": "real-token-abc123"}))

    await ray_manager._write_token_securely({"credential_ref": "cp-abc"})

    # Exactly one HTTP GET, with the node credential as Bearer token.
    ray_manager._http.get.assert_awaited_once()
    (url,), kwargs = ray_manager._http.get.call_args
    assert url.endswith("/api/admin/system/nodes/secrets/cp-abc")
    assert kwargs["headers"]["Authorization"] == "Bearer cred-id.deadbeef"

    # The fetched token is written to disk with 0600 (no mock prefix).
    token = ray_manager._token_file.read_text()
    assert token == "real-token-abc123"
    assert not token.startswith("mock-token-")


@pytest.mark.asyncio()
async def test_write_token_reuses_fresh_token(ray_manager: RayManager) -> None:
    """Within the TTL and for the same credential_ref, no second fetch."""
    ray_manager._http = MagicMock()
    ray_manager._http.get = AsyncMock(return_value=_FakeResponse({"token": "t1"}))

    await ray_manager._write_token_securely({"credential_ref": "ref"})
    ray_manager._http.get.reset_mock()

    await ray_manager._write_token_securely({"credential_ref": "ref"})
    ray_manager._http.get.assert_not_awaited()


@pytest.mark.asyncio()
async def test_write_token_refetches_on_new_ref(ray_manager: RayManager) -> None:
    ray_manager._http = MagicMock()
    ray_manager._http.get = AsyncMock(return_value=_FakeResponse({"token": "t1"}))

    await ray_manager._write_token_securely({"credential_ref": "ref-a"})
    ray_manager._http.get.reset_mock()

    await ray_manager._write_token_securely({"credential_ref": "ref-b"})
    ray_manager._http.get.assert_awaited_once()


@pytest.mark.asyncio()
async def test_write_token_requires_credential_ref(ray_manager: RayManager) -> None:
    ray_manager._http = MagicMock()
    with pytest.raises(ValueError, match="credential_ref"):
        await ray_manager._write_token_securely({})


@pytest.mark.asyncio()
async def test_write_token_requires_node_credential(state: StateStore, tmp_path: Path) -> None:
    state.state.credential = None
    manager = RayManager(
        state_store=state, events=EventBuffer(),
        ray_base_path="/opt/llm-port/ray", token_dir=tmp_path / "tok",
    )
    manager._http = MagicMock()
    with pytest.raises(RuntimeError, match="credential"):
        await manager._write_token_securely({"credential_ref": "ref"})


@pytest.mark.asyncio()
async def test_write_token_http_failure_raises_runtime_error(ray_manager: RayManager) -> None:
    ray_manager._http = MagicMock()
    ray_manager._http.get = AsyncMock(side_effect=OSError("conn refused"))
    with pytest.raises(RuntimeError, match="token fetch failed"):
        await ray_manager._write_token_securely({"credential_ref": "ref"})


# ---------------------------------------------------------------------------
# Ray 2.58 token auth env: RAY_AUTH_MODE + RAY_AUTH_TOKEN_PATH, no
# --redis-password-file flag.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_start_head_uses_ray_auth_env_vars(ray_manager: RayManager, fake_runtime: MagicMock) -> None:
    _stub_token_fetch(ray_manager)
    await ray_manager.start_head(
        {"credential_ref": "ref-1", "port": 6379, "dashboard_port": 8265, "dashboard_host": "127.0.0.1"},
        emit_progress=_noop_progress,
    )
    assert fake_runtime._exec.call_count == 1
    args = _last_exec_args(fake_runtime)
    env = _last_exec_env(fake_runtime)
    assert env["RAY_AUTH_MODE"] == "token"
    assert env["RAY_AUTH_TOKEN_PATH"] == str(ray_manager._token_file)
    # The old --redis-password-file flag must not be present.
    joined = " ".join(args)
    assert "--redis-password-file" not in joined
    # Head args are present.
    assert "--head" in args
    assert "--port=6379" in args


@pytest.mark.asyncio()
async def test_start_head_dashboard_disabled_flag(ray_manager: RayManager, fake_runtime: MagicMock) -> None:
    """include_dashboard=False emits --include-dashboard=false (no host/port)."""
    _stub_token_fetch(ray_manager)
    await ray_manager.start_head(
        {"credential_ref": "ref-1", "port": 6379, "include_dashboard": False},
        emit_progress=_noop_progress,
    )
    args = _last_exec_args(fake_runtime)
    assert "--include-dashboard=false" in args
    joined = " ".join(args)
    assert "--dashboard-host" not in joined
    assert "--dashboard-port" not in joined


@pytest.mark.asyncio()
async def test_start_head_dashboard_enabled_by_default(ray_manager: RayManager, fake_runtime: MagicMock) -> None:
    _stub_token_fetch(ray_manager)
    await ray_manager.start_head(
        {"credential_ref": "ref-1", "port": 6379, "dashboard_port": 8265, "dashboard_host": "10.1.1.1"},
        emit_progress=_noop_progress,
    )
    args = _last_exec_args(fake_runtime)
    assert "--dashboard-host=10.1.1.1" in args
    assert "--dashboard-port=8265" in args
    assert "--include-dashboard" not in args


@pytest.mark.asyncio()
async def test_start_head_num_resources_flags(ray_manager: RayManager, fake_runtime: MagicMock) -> None:
    _stub_token_fetch(ray_manager)
    await ray_manager.start_head(
        {"credential_ref": "ref-1", "port": 6379, "num_cpus": 4, "num_gpus": 2},
        emit_progress=_noop_progress,
    )
    args = _last_exec_args(fake_runtime)
    assert "--num-cpus=4" in args
    assert "--num-gpus=2" in args


@pytest.mark.asyncio()
async def test_join_cluster_uses_ray_auth_env_vars(ray_manager: RayManager, fake_runtime: MagicMock) -> None:
    _stub_token_fetch(ray_manager)
    await ray_manager.join_cluster(
        {
            "head_address": "10.0.0.5:6379",
            "credential_ref": "ref-1",
            "node_ip_address": "10.0.0.6",
        },
        emit_progress=_noop_progress,
    )
    assert fake_runtime._exec.call_count == 1
    args = _last_exec_args(fake_runtime)
    env = _last_exec_env(fake_runtime)
    assert env.get("RAY_AUTH_MODE") == "token"
    assert env.get("RAY_AUTH_TOKEN_PATH") == str(ray_manager._token_file)
    assert "--address=10.0.0.5:6379" in args
    assert "--node-ip-address=10.0.0.6" in args
    assert "--head" not in args
    assert "--redis-password-file" not in " ".join(args)


# ---------------------------------------------------------------------------
# Version threading
# ---------------------------------------------------------------------------


def test_head_payload_accepts_version() -> None:
    spec = StartRayHeadPayload.model_validate({"credential_ref": "ref", "version": "2.57.0"})
    assert spec.version == "2.57.0"


def test_head_payload_defaults_version_and_dashboard() -> None:
    spec = StartRayHeadPayload.model_validate({"credential_ref": "ref"})
    assert spec.version == "2.58.0"
    assert spec.include_dashboard is True


def test_join_payload_accepts_version() -> None:
    spec = JoinRayClusterPayload.model_validate(
        {"head_address": "1.2.3.4:6379", "credential_ref": "r", "version": "2.57.1"}
    )
    assert spec.version == "2.57.1"


def test_stop_payload_accepts_version() -> None:
    spec = StopRayPayload.model_validate({"force": True, "version": "2.57.2"})
    assert spec.version == "2.57.2"
    assert spec.force is True


def test_ensure_runtime_payload_defaults() -> None:
    spec = EnsureRayRuntimePayload.model_validate({})
    assert spec.version == "2.58.0"


@pytest.mark.asyncio()
async def test_start_head_uses_payload_version(ray_manager: RayManager, fake_runtime: MagicMock) -> None:
    _stub_token_fetch(ray_manager)
    await ray_manager.start_head(
        {"credential_ref": "r", "port": 6379, "version": "2.57.0"},
        emit_progress=_noop_progress,
    )
    assert fake_runtime._exec.call_args.kwargs["version"] == "2.57.0"


@pytest.mark.asyncio()
async def test_start_head_falls_back_to_default_version(ray_manager: RayManager, fake_runtime: MagicMock) -> None:
    _stub_token_fetch(ray_manager)
    await ray_manager.start_head(
        {"credential_ref": "r", "port": 6379},
        emit_progress=_noop_progress,
    )
    assert fake_runtime._exec.call_args.kwargs["version"] == "2.58.0"


@pytest.mark.asyncio()
async def test_stop_ray_uses_payload_version(ray_manager: RayManager, fake_runtime: MagicMock) -> None:
    result = await ray_manager.stop_ray({"force": True, "version": "2.57.3"})
    fake_runtime.stop.assert_awaited_once()
    assert fake_runtime.stop.call_args.kwargs["version"] == "2.57.3"
    assert fake_runtime.stop.call_args.kwargs["force"] is True
    assert result["stopped"] is True


@pytest.mark.asyncio()
async def test_stop_ray_unlinks_token_file(ray_manager: RayManager) -> None:
    (ray_manager._token_dir).mkdir(parents=True, exist_ok=True)
    ray_manager._token_file.write_text("tok")
    await ray_manager.stop_ray({"force": True})
    assert not ray_manager._token_file.exists()
    assert ray_manager._token_ref is None


# ---------------------------------------------------------------------------
# ensure_runtime: installed flag + CLI path + SDK version
# ---------------------------------------------------------------------------


def test_rayruntime_managed_binary_preferred_when_present(tmp_path: Path) -> None:
    """A managed <base>/<version>/bin/ray (present + executable) wins."""
    base = tmp_path / "ray"
    managed = base / "2.58.0" / "bin" / "ray"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"#!/bin/sh\n")
    # On Unix os.access(X_OK) is the real gate; on Windows it is advisory
    # (existence suffices). Make it executable where that means something.
    if os.name != "nt":
        os.chmod(str(managed), 0o755)
    rt = RayRuntime(ray_base_path=str(base))
    assert rt.ray_binary_path("2.58.0") == managed


def test_rayruntime_missing_binary_falls_back_to_managed_marker(tmp_path: Path, monkeypatch) -> None:
    """With no managed dir, no ray on PATH, no venv hit: the managed path for
    the requested version is returned as a not-found marker."""
    rt = RayRuntime(ray_base_path=str(tmp_path / "ray-none"))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("llm_port_node_agent.ray.runtime._venv_ray_binaries", lambda: [])
    result = rt.ray_binary_path("2.58.0")
    assert result == tmp_path / "ray-none" / "2.58.0" / "bin" / "ray"
    assert not result.exists()


# ---------------------------------------------------------------------------
# stop semantics (best-effort, never raises)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_stop_is_best_effort_never_raises() -> None:
    """A failing `ray stop` is reported, never raised (idempotency rule)."""
    rt = RayRuntime(ray_base_path="/opt/llm-port/ray")
    rt._exec = AsyncMock(side_effect=errors.RayRuntimeError("stop failed (exit 1): something"))
    result = await rt.stop(version="2.58.0", force=True)
    assert result == {"stopped": False, "best_effort": True, "detail": "stop failed (exit 1): something"}


@pytest.mark.asyncio()
async def test_stop_success_path() -> None:
    rt = RayRuntime(ray_base_path="/opt/llm-port/ray")
    rt._exec = AsyncMock(return_value=MagicMock(returncode=0, stdout=b"", stderr=b""))
    result = await rt.stop(version="2.58.0")
    assert result == {"stopped": True}


@pytest.mark.asyncio()
async def test_leave_cluster_stops_ray(ray_manager: RayManager, fake_runtime: MagicMock) -> None:
    result = await ray_manager.leave_cluster({})
    fake_runtime.stop.assert_awaited_once()
    assert result["left"] is True
    assert ray_manager._core.disconnect.called
