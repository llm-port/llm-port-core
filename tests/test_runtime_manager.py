"""Tests for RuntimeManager — Docker arg sanitization, GPU, resource limits."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.runtime_manager import RuntimeManager, RuntimeManagerError
from llm_port_node_agent.state_store import StateStore


class FakeRuntime:
    """In-memory runtime that records calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.name = "fake"

    async def run(self, **kwargs: Any) -> str:
        self.calls.append(("run", (), kwargs))
        return "container123"

    async def start(self, name: str, **kw: Any) -> None:
        self.calls.append(("start", (name,), kw))

    async def stop(self, name: str, **kw: Any) -> None:
        self.calls.append(("stop", (name,), kw))

    async def restart(self, name: str, **kw: Any) -> None:
        self.calls.append(("restart", (name,), kw))

    async def remove(self, name: str, **kw: Any) -> None:
        self.calls.append(("remove", (name,), kw))

    async def inspect(self, name: str, **kw: Any) -> dict:
        self.calls.append(("inspect", (name,), kw))
        return {"__missing": True}

    async def exists(self, name: str) -> bool:
        self.calls.append(("exists", (name,), {}))
        return False

    async def port(self, name: str, container_port: str, **kw: Any) -> str | None:
        self.calls.append(("port", (name, container_port), kw))
        return "32001"

    async def logs(self, name: str, **kw: Any) -> tuple[int, str]:
        self.calls.append(("logs", (name,), kw))
        return (0, "")

    async def ps(self, **kw: Any) -> list[str]:
        self.calls.append(("ps", (), kw))
        return []

    async def images(self, **kw: Any) -> list[str]:
        self.calls.append(("images", (), kw))
        return []

    async def pull(self, image: str, **kw: Any) -> None:
        self.calls.append(("pull", (image,), kw))

    async def load_image_tar(self, stream: Any, **kw: Any) -> str:
        self.calls.append(("load_image_tar", (stream,), kw))
        return "Loaded"

    async def is_available(self) -> bool:
        return True

    def run_kwargs(self) -> dict[str, Any]:
        """Return kwargs from the first ``run`` call."""
        for method, _, kwargs in self.calls:
            if method == "run":
                return kwargs
        return {}


@pytest.fixture()
def fake_runtime() -> FakeRuntime:
    return FakeRuntime()


@pytest.fixture()
def manager(tmp_path: Path, fake_runtime: FakeRuntime) -> RuntimeManager:
    store = StateStore(tmp_path / "state.json")
    events = EventBuffer()
    return RuntimeManager(
        runtime=fake_runtime,
        state_store=store,
        events=events,
        advertise_host="10.0.0.1",
        advertise_scheme="http",
    )


def test_sanitize_rejects_privileged() -> None:
    with pytest.raises(RuntimeManagerError, match="--privileged"):
        RuntimeManager._sanitize_command_args(["--privileged"])


def test_sanitize_rejects_cap_add() -> None:
    with pytest.raises(RuntimeManagerError, match="--cap-add"):
        RuntimeManager._sanitize_command_args(["--cap-add=SYS_ADMIN"])


def test_sanitize_rejects_pid_host() -> None:
    with pytest.raises(RuntimeManagerError, match="--pid"):
        RuntimeManager._sanitize_command_args(["--pid=host"])


def test_sanitize_rejects_volume_mount() -> None:
    with pytest.raises(RuntimeManagerError, match="Volume mounts"):
        RuntimeManager._sanitize_command_args(["-v", "/host:/container"])


def test_sanitize_rejects_network_host() -> None:
    with pytest.raises(RuntimeManagerError, match="Host network"):
        RuntimeManager._sanitize_command_args(["--network=host"])


def test_sanitize_allows_clean_args() -> None:
    # Should not raise
    RuntimeManager._sanitize_command_args(["--model", "/models/llama.gguf", "--port", "8080"])


def test_container_name_is_deterministic() -> None:
    runtime_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    name = RuntimeManager._container_name(runtime_id, runtime_name="test-model")
    assert name == "llm-port-test-model"
    # Same name regardless of runtime_id
    name2 = RuntimeManager._container_name("11111111-2222-3333-4444-555555555555", runtime_name="test-model")
    assert name == name2


@pytest.mark.asyncio()
async def test_deploy_workload_includes_gpu_flag(manager: RuntimeManager, fake_runtime: FakeRuntime) -> None:
    """Verify GPU and memory settings are passed to the runtime."""
    result = await manager.deploy_workload({
        "runtime_id": "rt-001",
        "provider_type": "vllm",
        "image": "vllm/vllm-openai:latest",
        "gpu_request": "all",
        "memory_limit": "16g",
    })
    assert result["runtime_id"] == "rt-001"

    kw = fake_runtime.run_kwargs()
    assert kw["gpus"] == "all"
    assert "--memory" in (kw.get("extra_args") or [])
    assert "16g" in (kw.get("extra_args") or [])
    # Default shm for GPU workloads
    assert "--shm-size" in (kw.get("extra_args") or [])
    assert "1g" in (kw.get("extra_args") or [])


@pytest.mark.asyncio()
async def test_deploy_workload_custom_port(manager: RuntimeManager, fake_runtime: FakeRuntime) -> None:
    """Verify custom container_port is used."""
    await manager.deploy_workload({
        "runtime_id": "rt-002",
        "image": "test:latest",
        "container_port": "3000",
    })
    kw = fake_runtime.run_kwargs()
    assert "3000" in (kw.get("ports") or [])


@pytest.mark.asyncio()
async def test_deploy_workload_ipc_mode_default(manager: RuntimeManager, fake_runtime: FakeRuntime) -> None:
    """vLLM with GPU defaults to --ipc host."""
    await manager.deploy_workload({
        "runtime_id": "rt-003",
        "provider_type": "vllm",
        "image": "vllm/vllm-openai:latest",
    })
    kw = fake_runtime.run_kwargs()
    # gpu defaults to all for vllm, ipc defaults to host for vllm+gpu
    assert kw["gpus"] == "all"
    extra = kw.get("extra_args") or []
    assert "--ipc" in extra
    idx = extra.index("--ipc")
    assert extra[idx + 1] == "host"
