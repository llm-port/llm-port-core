"""Tests for CommandDispatcher — routing, idempotency, policy."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_port_node_agent.dispatcher import CommandDispatcher
from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.policy_guard import PolicyGuard, PolicyViolationError
from llm_port_node_agent.runtime_manager import RuntimeManagerError
from llm_port_node_agent.state_store import StateStore


@pytest.fixture()
def parts(tmp_path: Path) -> dict[str, Any]:
    store = StateStore(tmp_path / "state.json")
    events = EventBuffer()
    runtime = MagicMock()
    guard = PolicyGuard()
    refresh_cb = MagicMock()
    dispatcher = CommandDispatcher(
        state_store=store,
        runtime_manager=runtime,
        policy_guard=guard,
        events=events,
        on_refresh_inventory=refresh_cb,
    )
    return {
        "dispatcher": dispatcher,
        "store": store,
        "runtime": runtime,
        "guard": guard,
        "refresh_cb": refresh_cb,
        "events": events,
    }


@pytest.fixture()
def emit() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio()
async def test_missing_command_id(parts: dict, emit: AsyncMock) -> None:
    result = await parts["dispatcher"].handle({"command_type": "deploy_workload"}, emit)
    assert result["success"] is False
    assert result["error_code"] == "invalid_command"


@pytest.mark.asyncio()
async def test_deploy_routes_to_runtime(parts: dict, emit: AsyncMock) -> None:
    parts["runtime"].deploy_workload = AsyncMock(return_value={"runtime_id": "r1"})
    result = await parts["dispatcher"].handle(
        {"id": "cmd-1", "command_type": "deploy_workload", "payload": {"image": "test:latest"}},
        emit,
    )
    assert result["success"] is True
    assert result["result"]["runtime_id"] == "r1"
    parts["runtime"].deploy_workload.assert_awaited_once()


@pytest.mark.asyncio()
async def test_idempotent_replay(parts: dict, emit: AsyncMock) -> None:
    parts["runtime"].deploy_workload = AsyncMock(return_value={"runtime_id": "r1"})
    cmd = {"id": "cmd-2", "command_type": "deploy_workload", "payload": {"image": "test:latest"}}
    first = await parts["dispatcher"].handle(cmd, emit)
    second = await parts["dispatcher"].handle(cmd, emit)
    assert second["result"].get("replayed") is True
    # deploy called only once
    assert parts["runtime"].deploy_workload.await_count == 1


@pytest.mark.asyncio()
async def test_policy_violation_returns_error(parts: dict, emit: AsyncMock) -> None:
    parts["guard"].validate = MagicMock(side_effect=PolicyViolationError("denied"))
    result = await parts["dispatcher"].handle(
        {"id": "cmd-3", "command_type": "deploy_workload", "payload": {}},
        emit,
    )
    assert result["success"] is False
    assert result["error_code"] == "policy_violation"


@pytest.mark.asyncio()
async def test_runtime_error_returns_error(parts: dict, emit: AsyncMock) -> None:
    parts["runtime"].deploy_workload = AsyncMock(side_effect=RuntimeManagerError("fail"))
    result = await parts["dispatcher"].handle(
        {"id": "cmd-4", "command_type": "deploy_workload", "payload": {"image": "test:latest"}},
        emit,
    )
    assert result["success"] is False
    assert result["error_code"] == "runtime_error"


@pytest.mark.asyncio()
async def test_refresh_inventory_calls_callback(parts: dict, emit: AsyncMock) -> None:
    result = await parts["dispatcher"].handle(
        {"id": "cmd-5", "command_type": "refresh_inventory", "payload": {}},
        emit,
    )
    assert result["success"] is True
    assert result["result"]["refresh_requested"] is True
    parts["refresh_cb"].assert_called_once()


@pytest.mark.asyncio()
async def test_image_allowlist_rejects(parts: dict, emit: AsyncMock) -> None:
    guard = PolicyGuard(image_allowlist=["ghcr.io/my-org/"])
    parts["dispatcher"]._guard = guard
    parts["runtime"].deploy_workload = AsyncMock(return_value={"runtime_id": "r2"})
    result = await parts["dispatcher"].handle(
        {"id": "cmd-6", "command_type": "deploy_workload", "payload": {"image": "evil:latest"}},
        emit,
    )
    assert result["success"] is False
    assert result["error_code"] == "policy_violation"


@pytest.mark.asyncio()
async def test_image_allowlist_allows(parts: dict, emit: AsyncMock) -> None:
    guard = PolicyGuard(image_allowlist=["ghcr.io/my-org/"])
    parts["dispatcher"]._guard = guard
    parts["runtime"].deploy_workload = AsyncMock(return_value={"runtime_id": "r3"})
    result = await parts["dispatcher"].handle(
        {"id": "cmd-7", "command_type": "deploy_workload", "payload": {"image": "ghcr.io/my-org/model:v1"}},
        emit,
    )
    assert result["success"] is True


@pytest.mark.asyncio()
async def test_events_emitted(parts: dict, emit: AsyncMock) -> None:
    parts["runtime"].deploy_workload = AsyncMock(return_value={"runtime_id": "r4"})
    await parts["dispatcher"].handle(
        {"id": "cmd-8", "command_type": "deploy_workload", "payload": {"image": "test:latest"}},
        emit,
    )
    events = parts["events"].drain()
    assert any(e.get("event_type") == "command.finished" for e in events)


@pytest.mark.asyncio()
async def test_inflight_redispatch_awaits_same_task(parts: dict, emit: AsyncMock) -> None:
    """A re-dispatched copy arriving mid-execution must not re-execute.

    When the node reconnects, the (new) agent stream re-dispatches any
    in-flight past-timeout commands. If the original is still running on the
    agent, the second copy must join the in-flight task rather than run the
    workload a second time.
    """
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _slow_deploy(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"runtime_id": "r-inflight"}

    parts["runtime"].deploy_workload = AsyncMock(side_effect=_slow_deploy)
    cmd = {
        "id": "cmd-9",
        "command_type": "deploy_workload",
        "payload": {"image": "test:latest"},
    }
    first = asyncio.create_task(parts["dispatcher"].handle(cmd, emit))
    await entered.wait()  # original is now mid-execution

    # A re-dispatched copy of the same command (e.g. from the new stream after
    # a reconnect) arrives while the first is still running. It must join the
    # in-flight task, so run it as a concurrent task.
    second_task = asyncio.create_task(
        parts["dispatcher"].handle(dict(cmd, redispatch=True), emit)
    )
    await asyncio.sleep(0)  # let the second handle observe the in-flight task

    release.set()
    first_result, second = await asyncio.gather(first, second_task)

    # Both callers observe the same (single) execution outcome.
    assert first_result["success"] is True
    assert second["success"] is True
    assert second["result"]["runtime_id"] == "r-inflight"
    # deploy_workload ran exactly once, not twice.
    assert parts["runtime"].deploy_workload.await_count == 1
