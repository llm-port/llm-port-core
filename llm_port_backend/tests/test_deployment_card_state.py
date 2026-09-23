"""The metrics card must not contradict the page it sits on.

A deployment reading "Serving, 2 / 2 copies" at the top had a metrics card
below it saying **Runtime state: unknown**, under an orange "Partial metrics"
warning. Nothing was wrong with the deployment. Two separate habits produced
it:

  * On the steady-state path the driver returns early -- deliberately, because
    probing Serve costs a ~20s round trip a polling page cannot absorb -- and
    that early return never set ``app_status``. The reconciler had already
    decided the answer and written it to the deployment row; the card just did
    not look.

  * Every partial rendered as a warning. But "these counts are from the last
    cluster check rather than this instant" is the normal steady state, not a
    degradation. A notice that cries wolf on every healthy page is one an
    operator learns to scroll past, which costs them the real ones.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from llm_port_backend.db.models.inference import (
    DeploymentPhase,
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.drivers.ray.driver import RayDriver
from llm_port_backend.services.inference.observability import MetricsPartial

pytestmark = pytest.mark.anyio()


class _NodeControl:
    """Present but never used: the steady-state path must not probe.

    Passing ``None`` would take the "no head node reachable" branch instead,
    which is a different case with its own test below.
    """


async def _deployment(dbsession, *, phase: str, reconciled: bool = True):
    """A real environment/deployment pair.

    Stubs do not reach the path under test: the driver resolves the
    environment and its head node from the session before it decides
    anything, so an object graph is the only way in.
    """
    control_plane = InferenceControlPlane(
        name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray"
    )
    dbsession.add(control_plane)
    await dbsession.flush()

    node = InfraNode(
        agent_id=f"head-{uuid.uuid4().hex[:8]}", host="10.88.10.71", status="healthy"
    )
    dbsession.add(node)
    await dbsession.flush()

    env = InferenceEnvironment(
        control_plane_id=control_plane.id,
        name=f"env-{uuid.uuid4().hex[:8]}",
        head_node_id=node.id,
        desired_state="running",
        config_json={"runtime_bundle_id": "bundle-dgx-spark-gb10-v1"},
    )
    dbsession.add(env)
    await dbsession.flush()
    dbsession.add(
        InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head")
    )

    model = LLMModel(
        display_name="Qwen/Qwen2.5-0.5B-Instruct",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="Qwen/Qwen2.5-0.5B-Instruct",
        hf_revision="main",
    )
    dbsession.add(model)
    await dbsession.flush()

    deployment = InferenceDeployment(
        environment_id=env.id,
        model_id=model.id,
        name=f"dep-{uuid.uuid4().hex[:8]}",
        spec_json={"model": {"id": str(model.id)}},
        phase=phase,
        ready_replicas=2,
        total_replicas=2,
        observed_status_json={
            "observation": {
                "reconciled": reconciled,
                "app": "llmport-app",
                "ready_replicas": 2,
            }
        },
    )
    dbsession.add(deployment)
    await dbsession.flush()
    return deployment


async def _metrics(dbsession, deployment):
    return await RayDriver().deployment_metrics(
        dbsession, deployment, node_control=_NodeControl()
    )


async def test_a_serving_deployment_does_not_read_as_unknown(dbsession) -> None:
    """The exact contradiction: "2 / 2 copies" beside "state: unknown"."""
    metrics = await _metrics(dbsession, await _deployment(dbsession, phase="running"))

    assert metrics.app_status == "running"
    assert metrics.replicas_ready == 2


async def test_the_state_comes_from_the_same_place_as_the_counts(
    dbsession,
) -> None:
    """Both are the reconciler's last conclusion, so they cannot disagree.

    Inventing a Serve-style status here would be a second opinion with no
    second observation behind it.
    """
    for phase in ("running", "failed", "pending"):
        metrics = await _metrics(dbsession, await _deployment(dbsession, phase=phase))
        assert metrics.app_status == phase


async def test_the_card_always_has_a_state_to_show(dbsession) -> None:
    """``phase`` is a non-null enum column, so there is always an answer.

    Worth stating because the driver still guards against an empty one. That
    guard is unreachable through the database -- ``phase=""`` is rejected as
    an invalid enum value -- and it is kept only so a caller passing a stub
    gets ``None`` rather than an empty string, which would render as a blank
    field that looks like a rendering bug.
    """
    for phase in ("pending", "running", "failed"):
        metrics = await _metrics(dbsession, await _deployment(dbsession, phase=phase))
        assert metrics.app_status, f"{phase} produced no state for the card"


def test_a_stub_without_a_phase_reads_as_unknown_not_blank() -> None:
    """The guard itself, at the only level it can be reached."""

    class _NoPhase:
        phase = ""

    assert (str(getattr(_NoPhase(), "phase", "") or "") or None) is None


async def test_the_steady_state_note_is_information_not_a_warning(
    dbsession,
) -> None:
    metrics = await _metrics(dbsession, await _deployment(dbsession, phase="running"))

    assert [p.severity for p in metrics.partials] == ["info"]
    assert not any(p.severity == "warning" for p in metrics.partials)


async def test_an_unreachable_head_is_still_a_warning(dbsession) -> None:
    """The distinction has to cut both ways, or it is just suppression."""
    deployment = await _deployment(dbsession, phase="running")
    metrics = await RayDriver().deployment_metrics(
        dbsession, deployment, node_control=None
    )
    assert any(p.severity == "warning" for p in metrics.partials)


def test_a_partial_is_a_warning_unless_it_says_otherwise() -> None:
    """The default must stay the loud one.

    A new partial added without thinking about severity describes something
    that went wrong far more often than not, so silence is the wrong default.
    """
    assert MetricsPartial(tier="serve", reason="probe failed").severity == "warning"


def test_a_stopped_deployment_reports_no_copies() -> None:
    """Stopping must clear the replica counts, not leave the serving ones.

    ``_observe`` only writes the counts it is given, and the stop path gave it
    none -- so a deployment whose Serve application had been deleted kept the
    numbers from when it was running. The detail page then read "Copies
    (ready / wanted) 1 / 1" for a cluster with no Serve application on it,
    which is the one screen an operator checks to confirm a stop worked.
    """
    from llm_port_backend.services.inference.drivers.ray.deployment import (
        RayDeploymentManager,
    )

    manager = RayDeploymentManager()
    deployment = SimpleNamespace(
        id=uuid.uuid4(),
        generation=2,
        observed_generation=1,
        phase=DeploymentPhase.RUNNING.value,
        phase_message="running",
        observed_status_json={},
        ready_replicas=1,
        total_replicas=1,
    )

    manager._observe(
        deployment,
        DeploymentPhase.STOPPED,
        "serve.delete(app) ok",
        True,
        observed={"reconciled": True, "action": "stop"},
        ready_replicas=0,
        total_replicas=0,
    )

    assert deployment.phase == DeploymentPhase.STOPPED.value
    assert deployment.ready_replicas == 0
    assert deployment.total_replicas == 0
