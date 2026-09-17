"""Tests for the node agent's Ray lifecycle module.

Covers the dispatcher's error-shape for Ray commands, the token-fetch
(``_write_token_securely``) against a mocked HTTP client, the ``ray start``
argument construction (Ray 2.58 token auth env vars, not
``--redis-password-file``), real ``ray list nodes`` JSON parsing, and version
threading through head/join/leave/stop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_port_node_agent.dispatcher import CommandDispatcher
from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.policy_guard import PolicyGuard
from llm_port_node_agent.runtime_manager import RuntimeManagerError
from llm_port_node_agent.state_store import StateStore
from llm_port_node_agent.ray.manager import RayManager
from llm_port_node_agent.ray.schemas import (
    JoinRayClusterPayload,
    StartRayHeadPayload,
    StopRayPayload,
)
from llm_port_node_agent.ray import status as ray_status_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeResponse:
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
        ray_base_path="/opt/llm-port/ray",
        token_dir=tmp_path / "ray-tokens",
    )


@pytest.fixture()
def ray_with_http(ray_manager: RayManager, state: StateStore) -> tuple[RayManager, MagicMock]:
    """Return (manager, http mock) with an async http client stubbed."""
    http = MagicMock()
    http.get = AsyncMock(
        return_value=_FakeResponse({"token": "real-token-abc123"}),
    )
    ray_manager._http = http
    return ray_manager, http


@pytest.fixture()
def fake_process(ray_manager: RayManager) -> MagicMock:
    """Replace ``manager._process`` with a mock that records args + env."""
    proc = MagicMock()
    proc.start = AsyncMock()
    proc.stop = AsyncMock()
    # ray_binary_path must return a mock whose .exists() is True so the
    # code proceeds past the binary-exists guard. A real Path can't be
    # patched (exists is read-only), so use a bare MagicMock.
    proc.ray_binary_path = MagicMock(return_value=MagicMock())
    ray_manager._process = proc
    return proc


def _ray_command(manager: CommandDispatcher, command_type: str, command_id: str = "cmd-ray-1", payload: dict | None = None) -> dict:
    return {"id": command_id, "command_type": command_type, "payload": payload or {}}


@pytest.fixture()
def dispatcher_with_ray(state: StateStore) -> CommandDispatcher:
    ray = RayManager(
        state_store=state,
        events=EventBuffer(),
        ray_base_path="/opt/llm-port/ray",
        token_dir=Path("/var/tmp/ray-tokens-test"),
        backend_url="http://127.0.0.1:8000",
    )
    return CommandDispatcher(
        state_store=state,
        runtime_manager=MagicMock(),
        policy_guard=PolicyGuard(),
        events=EventBuffer(),
        ray_manager=ray,
    )


# ---------------------------------------------------------------------------
# Dispatcher: Ray branches must RAISE so handle() normalizes success=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_dispatcher_ray_without_manager_reports_failure(dispatcher_with_ray: CommandDispatcher) -> None:
    """When ray_manager is None, the dispatcher must NOT report success=True."""
    # dispatcher_with_ray fixture includes a real RayManager — build a
    # second dispatcher with ray_manager=None to exercise the "not available"
    # path.
    store = dispatcher_with_ray._state
    d2 = CommandDispatcher(
        state_store=store,
        runtime_manager=MagicMock(),
        policy_guard=PolicyGuard(),
        events=EventBuffer(),
        ray_manager=None,
    )
    emit = AsyncMock()
    result = await d2.handle(_ray_command(d2, "ensure_ray_runtime", "cmd-none-1"), emit)
    assert result["success"] is False
    assert result["error_code"] == "runtime_error"


@pytest.mark.asyncio()
async def test_dispatcher_ray_error_is_normalized_not_double_wrapped(state: StateStore, emit: AsyncMock) -> None:
    """A RayManager that raises RuntimeManagerError must surface as
    success=False / error_code='runtime_error' — not as
    success=True/result={success:False}."""
    ray = RayManager(
        state_store=state, events=EventBuffer(),
        ray_base_path="/opt/llm-port/ray", token_dir=Path("/var/run/llm-port/ray"),
    )
    # Force start_head to raise a RuntimeManagerError.
    async def _boom(*a: Any, **kw: Any) -> dict:
        raise RuntimeManagerError("boom")

    ray.start_head = _boom  # type: ignore[method-assign]
    d = CommandDispatcher(
        state_store=state, runtime_manager=MagicMock(), policy_guard=PolicyGuard(),
        events=EventBuffer(), ray_manager=ray,
    )
    result = await d.handle(
        {"id": "cmd-ray-2", "command_type": "start_ray_head", "payload": {"credential_ref": "ref-1"}},
        emit,
    )
    assert result["success"] is False
    assert result["error_code"] == "runtime_error"
    assert result["error_message"] == "boom"
    assert "result" not in result or result.get("result") in (None, {})


@pytest.mark.asyncio()
async def test_dispatcher_ray_success_path(state: StateStore) -> None:
    """When the RayManager returns a dict, handle() wraps it as success=True."""
    ray = RayManager(
        state_store=state, events=EventBuffer(),
        ray_base_path="/opt/llm-port/ray", token_dir=Path("/var/run/llm-port/ray"),
    )
    async def _ok(*a: Any, **kw: Any) -> dict:
        return {"cluster_address": "10.0.0.1:6379"}

    ray.start_head = _ok  # type: ignore[method-assign]
    d = CommandDispatcher(
        state_store=state, runtime_manager=MagicMock(), policy_guard=PolicyGuard(),
        events=EventBuffer(), ray_manager=ray,
    )
    emit = AsyncMock()
    result = await d.handle(
        {"id": "cmd-ray-ok", "command_type": "start_ray_head", "payload": {"credential_ref": "ref-1"}},
        emit,
    )
    assert result["success"] is True
    assert result["result"]["cluster_address"] == "10.0.0.1:6379"


# ---------------------------------------------------------------------------
# Token fetch: real HTTP call with Bearer node credential
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_write_token_fetches_from_backend(ray_with_http: tuple[RayManager, MagicMock]) -> None:
    manager, http = ray_with_http
    await manager._write_token_securely({"credential_ref": "cp-abc"})

    # Exactly one HTTP GET, with the node credential as Bearer token.
    http.get.assert_awaited_once()
    (url,), kwargs = http.get.call_args
    assert url.endswith("/api/admin/system/nodes/secrets/cp-abc")
    assert kwargs["headers"]["Authorization"] == "Bearer cred-id.deadbeef"

    # The fetched token is written to disk with 0600 (no mock prefix).
    token = manager._token_file.read_text()
    assert token == "real-token-abc123"
    assert not token.startswith("mock-token-")


@pytest.mark.asyncio()
async def test_write_token_requires_credential_ref(ray_with_http: tuple[RayManager, MagicMock]) -> None:
    manager, _ = ray_with_http
    with pytest.raises(ValueError, match="credential_ref"):
        await manager._write_token_securely({})


@pytest.mark.asyncio()
async def test_write_token_requires_node_credential(state: StateStore) -> None:
    state.state.credential = None
    manager = RayManager(
        state_store=state, events=EventBuffer(),
        ray_base_path="/opt/llm-port/ray", token_dir=Path("/var/run/llm-port/ray"),
    )
    http = MagicMock()
    http.get = AsyncMock(return_value=_FakeResponse({"token": "x"}))
    manager._http = http
    with pytest.raises(RuntimeError, match="credential"):
        await manager._write_token_securely({"credential_ref": "ref"})


# ---------------------------------------------------------------------------
# Ray 2.58 token-auth: RAY_AUTH_MODE + RAY_AUTH_TOKEN_PATH, no
# --redis-password-file flag.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_start_head_uses_ray_auth_env_vars(ray_with_http, fake_process: MagicMock) -> None:
    manager, http = ray_with_http
    http.get = AsyncMock(return_value=_FakeResponse({"token": "tok"}))
    await manager.start_head(
        {"credential_ref": "ref-1", "port": 6379, "dashboard_port": 8265, "dashboard_host": "127.0.0.1"},
        emit_progress=AsyncMock(),
    )
    # The `start` call must have been made (process.start is an AsyncMock,
    # and its kwargs include `env`).
    assert fake_process.start.call_count >= 1
    call_kwargs = fake_process.start.call_args.kwargs
    args = fake_process.start.call_args.args[0]
    env = call_kwargs.get("env") or {}
    assert "RAY_AUTH_MODE" in env
    assert env["RAY_AUTH_MODE"] == "token"
    # The token path env var must point at the manager's token file.
    assert env.get("RAY_AUTH_TOKEN_PATH") == str(manager._token_file)
    # The old --redis-password-file flag must not be present.
    joined = " ".join(args)
    assert "--redis-password-file" not in joined
    # Head args are still present.
    assert "--head" in args
    assert f"--port={6379}" in args


@pytest.mark.asyncio()
async def test_join_cluster_uses_ray_auth_env_vars(ray_with_http, fake_process: MagicMock) -> None:
    manager, http = ray_with_http
    http.get = AsyncMock(return_value=_FakeResponse({"token": "tok"}))
    await manager.join_cluster(
        {
            "head_address": "10.0.0.5:6379",
            "credential_ref": "ref-1",
            "node_ip_address": "10.0.0.6",
        },
        emit_progress=AsyncMock(),
    )
    assert fake_process.start.call_count >= 1
    args = fake_process.start.call_args.args[0]
    env = fake_process.start.call_args.kwargs.get("env") or {}
    assert env.get("RAY_AUTH_MODE") == "token"
    assert env.get("RAY_AUTH_TOKEN_PATH") == str(manager._token_file)
    assert f"--address=10.0.0.5:6379" in args
    assert "--node-ip-address=10.0.0.6" in args
    assert "--redis-password-file" not in " ".join(args)


# ---------------------------------------------------------------------------
# Version threading
# ---------------------------------------------------------------------------


def test_head_payload_accepts_version() -> None:
    spec = StartRayHeadPayload.model_validate({"credential_ref": "ref", "version": "2.57.0"})
    assert spec.version == "2.57.0"


def test_join_payload_accepts_version() -> None:
    spec = JoinRayClusterPayload.model_validate({"head_address": "1.2.3.4:6379", "credential_ref": "r", "version": "2.57.1"})
    assert spec.version == "2.57.1"


def test_stop_payload_accepts_version() -> None:
    spec = StopRayPayload.model_validate({"force": True, "version": "2.57.2"})
    assert spec.version == "2.57.2"


@pytest.mark.asyncio()
async def test_start_head_uses_payload_version(ray_with_http, fake_process: MagicMock) -> None:
    manager, _ = ray_with_http
    await manager.start_head(
        {"credential_ref": "r", "port": 6379, "version": "2.57.0"},
        emit_progress=AsyncMock(),
    )
    version_arg = fake_process.start.call_args.kwargs["version"]
    assert version_arg == "2.57.0"


@pytest.mark.asyncio()
async def test_start_head_falls_back_to_default_version(ray_with_http, fake_process: MagicMock) -> None:
    manager, _ = ray_with_http
    await manager.start_head(
        {"credential_ref": "r", "port": 6379},
        emit_progress=AsyncMock(),
    )
    version_arg = fake_process.start.call_args.kwargs["version"]
    assert version_arg == "2.58.0"


@pytest.mark.asyncio()
async def test_stop_ray_uses_payload_version(ray_with_http, fake_process: MagicMock) -> None:
    manager, _ = ray_with_http
    await manager.stop_ray({"force": True, "version": "2.57.3"})
    fake_process.stop.assert_awaited_once()
    version_arg = fake_process.stop.call_args.kwargs["version"]
    assert version_arg == "2.57.3"


# ---------------------------------------------------------------------------
# Real ray status parsing
# ---------------------------------------------------------------------------


@pytest.fixture()
def emit() -> AsyncMock:
    return AsyncMock()


def _fake_subprocess(stdout: bytes, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


@pytest.mark.asyncio()
async def test_ray_status_parses_ray_list_nodes_json(ray_manager: RayManager) -> None:
    sample = [
        {"node_id": "aabbcc", "node_ip": "10.0.0.1", "state": "ALIVE", "is_head_node": True, "resources_total": {"CPU": 8, "GPU": 1}},
        {"node_id": "ddeeff", "node_ip": "10.0.0.2", "state": "ALIVE", "is_head_node": False, "resources_total": {"CPU": 8, "GPU": 1}},
        {"node_id": "001122", "node_ip": "10.0.0.3", "state": "DEAD", "is_head_node": False, "resources_total": {}},
    ]
    # Make the binary "exist" so status() proceeds to invoke ray list nodes.
    binary_mock = MagicMock()
    binary_mock.exists = MagicMock(return_value=True)
    ray_manager._process.ray_binary_path = MagicMock(return_value=binary_mock)
    payload = json.dumps(sample).encode("utf-8")
    with mock.patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_subprocess(payload, 0)),
    ) as patch_exec:
        result = await ray_status_module.get_ray_status(
            ray_manager._process, version="2.58.0", address=None,
        )
    # ray list nodes was invoked (with --format json).
    assert patch_exec.call_count == 1
    arg_str = " ".join(getattr(a, "__str__", lambda: str(a))() for a in (list(patch_exec.call_args.args) if patch_exec.call_args else []))
    assert "list" in arg_str and "nodes" in arg_str and "json" in arg_str
    assert result.alive is True
    assert result.num_nodes == 2  # only ALIVE nodes counted
    assert result.total_gpus == 2.0
    assert len(result.nodes) == 2
    head = next(n for n in result.nodes if n.get("is_head"))
    assert head["node_ip"] == "10.0.0.1"


@pytest.mark.asyncio()
async def test_ray_status_returns_dead_when_ray_list_fails(ray_manager: RayManager) -> None:
    binary_mock = MagicMock()
    binary_mock.exists = MagicMock(return_value=True)
    ray_manager._process.ray_binary_path = MagicMock(return_value=binary_mock)
    with mock.patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_subprocess(b"error: no cluster", 1)),
    ):
        result = await ray_status_module.get_ray_status(
            ray_manager._process, version="2.58.0", address=None,
        )
    assert result.alive is False
    assert result.num_nodes == 0


@pytest.mark.asyncio()
async def test_ray_status_returns_dead_when_ray_binary_missing(ray_manager: RayManager) -> None:
    missing = MagicMock()
    missing.exists = MagicMock(return_value=False)
    ray_manager._process.ray_binary_path = MagicMock(return_value=missing)
    result = await ray_status_module.get_ray_status(
        ray_manager._process, version="2.58.0", address=None,
    )
    assert result.alive is False


@pytest.mark.asyncio()
async def test_ray_status_includes_cluster_address_from_head(ray_manager: RayManager) -> None:
    sample = [
        {"node_id": "aabbcc", "node_ip": "10.9.9.9", "state": "ALIVE", "is_head_node": True, "node_manager_port": 6379, "resources_total": {}},
    ]
    binary_mock = MagicMock()
    binary_mock.exists = MagicMock(return_value=True)
    ray_manager._process.ray_binary_path = MagicMock(return_value=binary_mock)
    payload = json.dumps(sample).encode("utf-8")
    with mock.patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_subprocess(payload, 0)),
    ):
        result = await ray_status_module.get_ray_status(
            ray_manager._process, version="2.58.0", address=None,
        )
    assert result.alive is True
    assert result.cluster_address == "10.9.9.9:6379"
