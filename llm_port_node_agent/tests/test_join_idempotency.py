"""Joining a cluster twice must not add a second node to it.

Found on the DGX pair: four "alive" Ray nodes for two machines, and three
raylet processes inside one container. Each reconcile ran `ray start
--address` again, and Ray does not refuse that -- it starts another raylet.
The phantom nodes are Alive in GCS, advertise a GPU, and attract placement
they can never serve, which is how a Serve proxy ended up on a node the
control plane did not think existed.

`tolerate_already_running` never covered this, because nothing failed.
"""

from __future__ import annotations

from typing import Any

import pytest

from llm_port_node_agent.ray.container import (
    RayContainerRuntime,
    RuntimeBundleSpec,
)

HEAD = "10.100.0.2:6379"


def _spec() -> RuntimeBundleSpec:
    return RuntimeBundleSpec(
        image="llm-port/ray-runtime:test",
        digest="sha256:" + "0" * 64,
        name="llm-port-ray-runtime",
    )


class _FakeHandler:
    """Answers `cat`, `pgrep` and `ray` without a container."""

    def __init__(self, *, joined: str | None, raylets: int) -> None:
        self.joined = joined
        self.raylets = raylets
        self.commands: list[list[str]] = []

    async def exec_(
        self, _name: str, command: list[str], **_: Any
    ) -> tuple[int, str, str]:
        self.commands.append(command)
        if command[:1] == ["cat"]:
            return (0, self.joined, "") if self.joined else (1, "", "No such file")
        if command[:1] == ["pgrep"]:
            return (0, str(self.raylets), "")
        if command[:1] == ["ray"]:
            return (0, "ok", "")
        return (0, "", "")

    def ray_starts(self) -> list[list[str]]:
        return [c for c in self.commands if c[:2] == ["ray", "start"]]

    def ray_stops(self) -> list[list[str]]:
        return [c for c in self.commands if c[:2] == ["ray", "stop"]]


def _runtime(handler: _FakeHandler) -> RayContainerRuntime:
    runtime = RayContainerRuntime()
    runtime._handler = lambda _spec: handler  # type: ignore[assignment]
    return runtime


@pytest.mark.anyio()
async def test_a_node_already_in_this_cluster_does_not_start_another_raylet() -> None:
    """The bug, directly: this used to run `ray start --address` regardless."""
    handler = _FakeHandler(joined=HEAD, raylets=1)
    result = await _runtime(handler).join_cluster(_spec(), head_address=HEAD)

    assert result["joined"] is True
    assert result["already_member"] is True
    assert handler.ray_starts() == [], "a second raylet is a phantom cluster node"


@pytest.mark.anyio()
async def test_repeated_joins_stay_at_one_raylet() -> None:
    """Reconcile runs often; the fourth call must cost no more than the second."""
    handler = _FakeHandler(joined=HEAD, raylets=1)
    runtime = _runtime(handler)
    for _ in range(4):
        await runtime.join_cluster(_spec(), head_address=HEAD)
    assert handler.ray_starts() == []


@pytest.mark.anyio()
async def test_a_node_that_has_never_joined_does_start() -> None:
    handler = _FakeHandler(joined=None, raylets=0)
    result = await _runtime(handler).join_cluster(_spec(), head_address=HEAD)

    assert result["already_member"] is False
    assert len(handler.ray_starts()) == 1
    assert f"--address={HEAD}" in handler.ray_starts()[0]


@pytest.mark.anyio()
async def test_a_recorded_join_with_no_raylet_is_restarted() -> None:
    """The marker outlives the process, so it is not sufficient on its own."""
    handler = _FakeHandler(joined=HEAD, raylets=0)
    result = await _runtime(handler).join_cluster(_spec(), head_address=HEAD)

    assert result["already_member"] is False
    assert len(handler.ray_starts()) == 1


@pytest.mark.anyio()
async def test_a_node_joined_elsewhere_is_stopped_before_it_joins_here() -> None:
    """Otherwise the old registration stays alive next to the new one."""
    handler = _FakeHandler(joined="10.100.0.9:6379", raylets=1)
    await _runtime(handler).join_cluster(_spec(), head_address=HEAD)

    assert handler.ray_stops(), "the stale membership must be torn down"
    assert len(handler.ray_starts()) == 1
    # Order matters: stopping after starting would kill what we just started.
    stop_at = handler.commands.index(handler.ray_stops()[0])
    start_at = handler.commands.index(handler.ray_starts()[0])
    assert stop_at < start_at


@pytest.mark.anyio()
async def test_whitespace_in_the_marker_does_not_look_like_a_different_cluster() -> None:
    handler = _FakeHandler(joined=f"  {HEAD}\n", raylets=1)
    result = await _runtime(handler).join_cluster(_spec(), head_address=f"{HEAD}\n")
    assert result["already_member"] is True
    assert handler.ray_starts() == []


# ── the head path has the same hazard ────────────────────────────────────


@pytest.mark.anyio()
async def test_a_node_already_heading_this_cluster_does_not_start_a_second_head() -> None:
    handler = _FakeHandler(joined="10.100.0.2:6379", raylets=1)
    result = await _runtime(handler).start_head(
        _spec(), port=6379, dashboard_port=8265, dashboard_host="0.0.0.0",
        node_ip_address="10.100.0.2",
    )
    assert result["already_running"] is True
    assert handler.ray_starts() == [], "a second GCS in one container serves nothing"


@pytest.mark.anyio()
async def test_a_node_heading_another_cluster_is_stopped_first() -> None:
    """Creating a new cluster from the same fleet used to stack control planes.

    The DGX head reached three GCS servers and four raylets this way, and
    stopped serving entirely.
    """
    handler = _FakeHandler(joined="10.100.0.9:6379", raylets=1)
    await _runtime(handler).start_head(
        _spec(), port=6379, dashboard_port=8265, dashboard_host="0.0.0.0",
        node_ip_address="10.100.0.2",
    )
    assert handler.ray_stops(), "the previous control plane must be torn down"
    assert len(handler.ray_starts()) == 1
    assert handler.commands.index(handler.ray_stops()[0]) < handler.commands.index(
        handler.ray_starts()[0]
    )


@pytest.mark.anyio()
async def test_a_fresh_node_starts_a_head() -> None:
    handler = _FakeHandler(joined=None, raylets=0)
    result = await _runtime(handler).start_head(
        _spec(), port=6379, dashboard_port=8265, dashboard_host="0.0.0.0",
        node_ip_address="10.100.0.2",
    )
    assert result.get("already_running") is not True
    assert len(handler.ray_starts()) == 1
