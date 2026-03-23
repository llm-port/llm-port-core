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


@pytest.fixture()
def manager(tmp_path: Path) -> RuntimeManager:
    store = StateStore(tmp_path / "state.json")
    events = EventBuffer()
    return RuntimeManager(
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


def test_container_name_uses_last_12_chars() -> None:
    runtime_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    name = RuntimeManager._container_name(runtime_id, runtime_name="test-model")
    # Should use last 12 chars of runtime_id (with dashes stripped)
    stripped = runtime_id.replace("-", "")
    assert name.endswith(stripped[-12:])


@pytest.mark.asyncio()
async def test_deploy_workload_includes_gpu_flag(manager: RuntimeManager) -> None:
    """Verify --gpus flag appears when gpu_request is provided."""
    captured_args: list[str] = []

    async def fake_docker(*args: str, timeout_sec: float = 30, raise_on_error: bool = True) -> tuple[int, str, str]:
        captured_args.extend(args)
        if args[0] == "inspect":
            return (1, "", "not found")
        if args[0] == "run":
            return (0, "container123\n", "")
        if args[0] == "port":
            return (0, "0.0.0.0:32001\n", "")
        return (0, "", "")

    manager._docker = fake_docker  # type: ignore[assignment]

    result = await manager.deploy_workload({
        "runtime_id": "rt-001",
        "provider_type": "vllm",
        "image": "vllm/vllm-openai:latest",
        "gpu_request": "all",
        "memory_limit": "16g",
    })
    assert result["runtime_id"] == "rt-001"
    assert "--gpus" in captured_args
    assert "all" in captured_args
    assert "--memory" in captured_args
    assert "16g" in captured_args
    # Default shm for GPU workloads
    assert "--shm-size" in captured_args
    assert "1g" in captured_args


@pytest.mark.asyncio()
async def test_deploy_workload_custom_port(manager: RuntimeManager) -> None:
    """Verify custom container_port is used."""
    captured_args: list[str] = []

    async def fake_docker(*args: str, timeout_sec: float = 30, raise_on_error: bool = True) -> tuple[int, str, str]:
        captured_args.extend(args)
        if args[0] == "inspect":
            return (1, "", "")
        if args[0] == "run":
            return (0, "cid\n", "")
        if args[0] == "port":
            return (0, "0.0.0.0:32002\n", "")
        return (0, "", "")

    manager._docker = fake_docker  # type: ignore[assignment]

    await manager.deploy_workload({
        "runtime_id": "rt-002",
        "image": "test:latest",
        "container_port": "3000",
    })
    assert "3000" in captured_args


@pytest.mark.asyncio()
async def test_deploy_workload_ipc_mode_default(manager: RuntimeManager) -> None:
    """vLLM with GPU defaults to --ipc host."""
    captured_args: list[str] = []

    async def fake_docker(*args: str, timeout_sec: float = 30, raise_on_error: bool = True) -> tuple[int, str, str]:
        captured_args.extend(args)
        if args[0] == "inspect":
            return (1, "", "")
        if args[0] == "run":
            return (0, "cid\n", "")
        if args[0] == "port":
            return (0, "0.0.0.0:32003\n", "")
        return (0, "", "")

    manager._docker = fake_docker  # type: ignore[assignment]

    await manager.deploy_workload({
        "runtime_id": "rt-003",
        "provider_type": "vllm",
        "image": "vllm/vllm-openai:latest",
    })
    # gpu defaults to all for vllm, ipc defaults to host for vllm+gpu
    assert "--gpus" in captured_args
    assert "--ipc" in captured_args
    idx = captured_args.index("--ipc")
    assert captured_args[idx + 1] == "host"
