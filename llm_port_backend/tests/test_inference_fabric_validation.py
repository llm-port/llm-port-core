"""Cheap active validation of a fabric candidate (Phase 4A, F-07).

The ephemeral TCP challenge existed as two well-written functions that nothing
ever called: there was no node command type for them and the planner never
issued one, so every recommendation rested on passive sysfs facts with no
proof the two nodes could reach each other on the chosen CIDR.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommand,
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.services.inference.planner import MultiNodeFabricPlanner


class _FakeGateway:
    """Records issued commands and answers them from a script."""

    def __init__(self, *, listen: dict[str, Any], connect: dict[str, Any]) -> None:
        self._results = {
            NodeCommandType.VALIDATE_FABRIC_LISTEN.value: listen,
            NodeCommandType.VALIDATE_FABRIC_CONNECT.value: connect,
        }
        self.issued: list[dict[str, Any]] = []
        self._rows: dict[uuid.UUID, InfraNodeCommand] = {}

    async def issue(self, **kwargs: Any) -> InfraNodeCommand:
        self.issued.append(kwargs)
        cmd_id = uuid.uuid4()
        row = InfraNodeCommand(
            id=cmd_id,
            node_id=uuid.UUID(str(kwargs["node_id"])),
            command_type=kwargs["command_type"],
            status=NodeCommandStatus.SUCCEEDED.value,
            idempotency_key=kwargs["idempotency_key"],
            result_json=dict(self._results.get(kwargs["command_type"], {})),
        )
        self._rows[cmd_id] = row
        return row

    async def wait(self, command_id: Any, *, budget_sec: float, **_: Any) -> InfraNodeCommand:
        return self._rows[uuid.UUID(str(command_id))]

    def by_type(self, command_type: str) -> list[dict[str, Any]]:
        return [c for c in self.issued if c["command_type"] == command_type]


async def _dgx_pair(dbsession: AsyncSession) -> InferenceEnvironment:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()

    def _caps(roce_ip: str) -> dict:
        return {
            "network": {
                "fabrics": [{
                    "interface": "enp1s0f1np1",
                    "ip": roce_ip,
                    "cidr": "10.100.0.0/24",
                    "speed_gbps": 200.0,
                    "link_type": "roce",
                    "rdma_device": "rocep1s0f1",
                    "mtu": 9000,
                    "is_up": True,
                    "has_default_route": False,
                }]
            }
        }

    n1 = InfraNode(agent_id="spark-3201", host="10.88.10.71", status="healthy",
                   capabilities_json=_caps("10.100.0.1"))
    n2 = InfraNode(agent_id="spark-ts3202", host="10.88.10.49", status="healthy",
                   capabilities_json=_caps("10.100.0.2"))
    dbsession.add_all([n1, n2])
    await dbsession.flush()
    dbsession.add_all([
        InferenceEnvironmentNode(environment_id=env.id, node_id=n1.id, role="head"),
        InferenceEnvironmentNode(environment_id=env.id, node_id=n2.id, role="worker"),
    ])
    await dbsession.flush()
    return env


@pytest.mark.anyio()
async def test_plan_certifies_the_recommendation_with_a_tcp_challenge(
    dbsession: AsyncSession,
) -> None:
    """The listener binds the candidate IP and the peer dials it from its own."""
    env = await _dgx_pair(dbsession)
    gateway = _FakeGateway(
        listen={"listening": True, "connected": True},
        connect={"reachable": True, "rtt_ms": 0.21},
    )

    plan = await MultiNodeFabricPlanner(dbsession, gateway=gateway).plan_environment(env.id)

    listens = gateway.by_type(NodeCommandType.VALIDATE_FABRIC_LISTEN.value)
    connects = gateway.by_type(NodeCommandType.VALIDATE_FABRIC_CONNECT.value)
    assert len(listens) == 1
    assert len(connects) == 1

    listen_payload = listens[0]["payload"]
    connect_payload = connects[0]["payload"]
    # Bound to the candidate's own addresses: a probe that leaves by the
    # default route would certify a link the plan is not about.
    assert {listen_payload["ip"], connect_payload["source_ip"]} == {"10.100.0.1", "10.100.0.2"}
    assert connect_payload["target_ip"] == listen_payload["ip"]
    assert 45460 <= listen_payload["port"] <= 45480
    assert connect_payload["probe_token"] == listen_payload["probe_token"]
    assert listen_payload["probe_token"]

    recommended = next(
        c for c in plan.candidates if c.candidate_id == plan.recommended_candidate_id
    )
    assert recommended.validation.performed is True
    assert recommended.validation.reachable is True
    assert recommended.validation.probe_results[0]["rtt_ms"] == 0.21
    assert not plan.blockers


@pytest.mark.anyio()
async def test_unreachable_candidate_becomes_a_blocker(dbsession: AsyncSession) -> None:
    """Passive facts can look perfect on a fabric that is not actually wired."""
    env = await _dgx_pair(dbsession)
    gateway = _FakeGateway(
        listen={"listening": True, "connected": False},
        connect={"reachable": False, "error": "TimeoutError"},
    )

    plan = await MultiNodeFabricPlanner(dbsession, gateway=gateway).plan_environment(env.id)

    recommended = next(
        c for c in plan.candidates if c.candidate_id == plan.recommended_candidate_id
    )
    assert recommended.validation.reachable is False
    assert plan.blockers
    assert "reachability challenge" in plan.blockers[0]


@pytest.mark.anyio()
async def test_apply_refuses_an_environment_the_replan_cannot_bind(
    dbsession: AsyncSession,
) -> None:
    """Apply re-derives the plan, so it also re-derives the blockers."""
    from llm_port_backend.services.inference.service import ConflictError

    env = await _dgx_pair(dbsession)
    planner = MultiNodeFabricPlanner(dbsession)
    plan = await planner.plan_environment(env.id, validate=False)
    assert not plan.blockers

    # The RoCE link goes down on one node between approval and apply.
    node_id = uuid.UUID(sorted(plan.inventory_revisions)[0])
    node = await dbsession.get(InfraNode, node_id)
    caps = {"network": {"fabrics": []}}
    node.capabilities_json = caps
    await dbsession.flush()

    with pytest.raises(ConflictError):
        await planner.apply_plan(env.id, plan)


@pytest.mark.anyio()
async def test_plan_without_a_gateway_says_so(dbsession: AsyncSession) -> None:
    """A passive-only plan must not look like a certified one."""
    env = await _dgx_pair(dbsession)
    plan = await MultiNodeFabricPlanner(dbsession).plan_environment(env.id)

    recommended = next(
        c for c in plan.candidates if c.candidate_id == plan.recommended_candidate_id
    )
    assert recommended.validation.performed is False
    assert recommended.validation.reachable is None
    assert any("passive" in w for w in plan.warnings)


@pytest.mark.anyio()
async def test_probe_failure_degrades_to_a_warning_not_an_error(
    dbsession: AsyncSession,
) -> None:
    """A gateway that cannot issue commands must not fail planning outright."""

    class _BrokenGateway:
        async def issue(self, **_: Any) -> Any:
            raise RuntimeError("node offline")

        async def wait(self, *_: Any, **__: Any) -> Any:  # pragma: no cover - unreachable
            raise AssertionError("wait must not be reached")

    env = await _dgx_pair(dbsession)
    plan = await MultiNodeFabricPlanner(dbsession, gateway=_BrokenGateway()).plan_environment(
        env.id
    )

    recommended = next(
        c for c in plan.candidates if c.candidate_id == plan.recommended_candidate_id
    )
    assert recommended.validation.performed is True
    assert recommended.validation.reachable is None
    assert any("inconclusive" in w for w in plan.warnings)
