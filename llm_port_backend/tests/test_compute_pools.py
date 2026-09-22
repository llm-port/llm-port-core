"""Compute pools are derived, never configured.

The point of the derivation is that a single-vendor cluster costs the operator
nothing -- one pool appears, nobody names it, nothing changes -- while a mixed
cluster gets the grouping that placement and runtime-bundle matching both
need.  If the derivation had to be maintained by hand it would be wrong the
first time someone added a node from the CLI.

The tests below pin the two properties the rest of the system leans on:

  * nodes of the same class land in one pool, whatever the pool is called;
  * nodes of different classes never do.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    InferenceComputePool,
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.pools import (
    ComputePoolCoordinator,
    accelerator_family,
    accelerator_vendor,
    cpu_architecture,
    pool_signature,
    suggest_pool_name,
)


def _node(
    *,
    vendor: str | None = "nvidia",
    family: str | None = "GB10",
    machine: str = "aarch64",
    gpu_count: int = 1,
    **extra: Any,
) -> InfraNode:
    """An unsaved node reporting what the agent's collectors report."""
    gpu: dict[str, Any] = {}
    if vendor is not None:
        gpu["vendor"] = vendor
    if family is not None:
        gpu["family"] = family
    caps: dict[str, Any] = {"machine": machine, "gpu_count": gpu_count}
    if gpu:
        caps["gpu"] = gpu
    caps.update(extra)
    return InfraNode(
        agent_id=f"agent-{uuid.uuid4().hex[:8]}",
        host="10.88.10.49",
        status="healthy",
        capabilities_json=caps,
    )


# ── derivation, no database needed ───────────────────────────────────────


class TestSignature:
    def test_reads_what_the_agent_reported(self) -> None:
        assert pool_signature(_node()) == "nvidia/aarch64/gb10"

    def test_arm64_and_aarch64_are_one_class(self) -> None:
        # Docker says arm64, uname says aarch64.  Two names, one architecture:
        # not collapsing them would split a homogeneous cluster in two.
        assert pool_signature(_node(machine="arm64")) == pool_signature(_node(machine="aarch64"))

    def test_amd64_and_x86_64_are_one_class(self) -> None:
        a = _node(machine="amd64", vendor="amd", family="MI300X")
        b = _node(machine="x86_64", vendor="amd", family="MI300X")
        assert pool_signature(a) == pool_signature(b)

    def test_vendor_separates(self) -> None:
        assert pool_signature(_node(vendor="amd")) != pool_signature(_node(vendor="nvidia"))

    def test_family_separates(self) -> None:
        # An H100 box and a GB10 box are both NVIDIA/x86-or-arm, and still not
        # interchangeable: the runtime bundle is built per compute capability.
        assert pool_signature(_node(family="H100")) != pool_signature(_node(family="GB10"))

    def test_unreported_family_is_still_a_class(self) -> None:
        assert pool_signature(_node(family=None)) == "nvidia/aarch64/any"

    def test_cpu_only_node(self) -> None:
        node = _node(vendor=None, family=None, gpu_count=0, machine="x86_64")
        assert accelerator_vendor(node) == "none"
        assert pool_signature(node) == "none/x86_64/any"

    def test_gpu_present_but_vendor_unreported(self) -> None:
        # Better an honest "unknown" class than folding it into NVIDIA's.
        node = _node(vendor=None, family=None, gpu_count=2)
        assert accelerator_vendor(node) == "unknown"

    def test_unknown_architecture_is_not_guessed(self) -> None:
        assert cpu_architecture(_node(machine="")) == "unknown"

    def test_family_falls_back_through_the_device_list(self) -> None:
        node = _node(family=None)
        node.capabilities_json["gpu"]["devices"] = [{"name": "Instinct MI300X"}]
        assert accelerator_family(node) == "Instinct MI300X"


class TestNames:
    def test_family_when_known(self) -> None:
        assert suggest_pool_name(_node()) == "gb10"

    def test_vendor_and_arch_otherwise(self) -> None:
        assert suggest_pool_name(_node(family=None)) == "nvidia-aarch64"

    def test_cpu_only(self) -> None:
        assert suggest_pool_name(_node(vendor=None, family=None, gpu_count=0)) == "cpu-only"


# ── assignment against the database ──────────────────────────────────────


async def _environment(session: AsyncSession) -> InferenceEnvironment:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    session.add(cp)
    await session.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:6]}",
        desired_state="running",
    )
    session.add(env)
    await session.flush()
    return env


async def _join(
    session: AsyncSession,
    env: InferenceEnvironment,
    node: InfraNode,
    *,
    role: str = "worker",
) -> tuple[InferenceEnvironmentNode, InferenceComputePool]:
    session.add(node)
    await session.flush()
    member = InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role=role)
    session.add(member)
    await session.flush()
    pool = await ComputePoolCoordinator(session).assign(
        environment_id=env.id, node=node, member=member
    )
    return member, pool


@pytest.mark.anyio()
async def test_identical_nodes_share_one_pool(dbsession: AsyncSession) -> None:
    env = await _environment(dbsession)
    _, first = await _join(dbsession, env, _node(), role="head")
    _, second = await _join(dbsession, env, _node())

    assert first.id == second.id
    pools = await ComputePoolCoordinator(dbsession).list_for_environment(env.id)
    assert len(pools) == 1, "a homogeneous cluster must not sprout a second pool"
    assert pools[0].name == "gb10"


@pytest.mark.anyio()
async def test_a_mixed_cluster_gets_one_pool_per_class(dbsession: AsyncSession) -> None:
    env = await _environment(dbsession)
    await _join(dbsession, env, _node(), role="head")
    await _join(dbsession, env, _node(vendor="amd", family="MI300X", machine="x86_64"))

    coordinator = ComputePoolCoordinator(dbsession)
    pools = await coordinator.list_for_environment(env.id)
    assert {p.signature for p in pools} == {"nvidia/aarch64/gb10", "amd/x86_64/mi300x"}
    assert await coordinator.member_counts(env.id) == {p.id: 1 for p in pools}


@pytest.mark.anyio()
async def test_members_are_counted_per_pool(dbsession: AsyncSession) -> None:
    env = await _environment(dbsession)
    _, nvidia = await _join(dbsession, env, _node(), role="head")
    await _join(dbsession, env, _node())
    _, amd = await _join(dbsession, env, _node(vendor="amd", family="MI300X", machine="x86_64"))

    counts = await ComputePoolCoordinator(dbsession).member_counts(env.id)
    assert counts[nvidia.id] == 2
    assert counts[amd.id] == 1


@pytest.mark.anyio()
async def test_renaming_a_pool_does_not_split_it(dbsession: AsyncSession) -> None:
    """Matching is on the signature, so the display name is free to change."""
    env = await _environment(dbsession)
    _, pool = await _join(dbsession, env, _node(), role="head")
    pool.name = "production-sparks"
    pool.managed = True
    await dbsession.flush()

    _, again = await _join(dbsession, env, _node())
    assert again.id == pool.id
    assert again.name == "production-sparks", "derivation must not rename a managed pool"


@pytest.mark.anyio()
async def test_a_name_collision_is_suffixed(dbsession: AsyncSession) -> None:
    """Two classes can suggest one name; the second gets a suffix, not a clash."""
    env = await _environment(dbsession)
    await _join(dbsession, env, _node(family=None), role="head")
    # Same vendor and architecture, different family -> different class, and
    # ``suggest_pool_name`` for the first was already "nvidia-aarch64".
    node = _node(family=None, machine="aarch64")
    node.capabilities_json["gpu"]["vendor"] = "nvidia"
    node.capabilities_json["gpu"]["model"] = "nvidia aarch64"
    await _join(dbsession, env, node)

    names = [p.name for p in await ComputePoolCoordinator(dbsession).list_for_environment(env.id)]
    assert len(names) == len(set(names)), f"duplicate pool names: {names}"


@pytest.mark.anyio()
async def test_pools_do_not_leak_between_environments(dbsession: AsyncSession) -> None:
    first = await _environment(dbsession)
    second = await _environment(dbsession)
    _, a = await _join(dbsession, first, _node(), role="head")
    _, b = await _join(dbsession, second, _node(), role="head")

    assert a.id != b.id
    rows = (
        await dbsession.execute(
            select(InferenceComputePool).where(InferenceComputePool.environment_id == first.id)
        )
    ).scalars().all()
    assert [p.id for p in rows] == [a.id]
