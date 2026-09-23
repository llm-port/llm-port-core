"""Scaling a deployment that is serving.

Found scaling the chat model on the DGX pair from the console:

* the request sat unnoticed for 93 s while the page read "1 / 1";
* for the whole scale-up the deployment read "Starting" with the message
  "app application DEPLOYING:", although its first copy served throughout;
* "wanted" was ready + pending, so a request for 3 read "2 / 2";
* asking for 3 copies on a cluster with 2 accelerators said the same thing,
  forever, with no reason.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.node_control import NodeCommandType
from llm_port_backend.services.inference import wakeup
from llm_port_backend.services.inference.drivers.ray.deployment import (
    _app_deployment_readiness,
    _scaling_message,
)
from tests.test_inference_ray import (
    _FakeNodeControl,
    _healthy_probe_result,
    _ray_driver,
    _run_serve_result,
    _serve_status,
    deployment_env,  # noqa: F401 - fixture
)


def _status(app: str, *, app_status: str, ready: int, pending: int, message: str = "") -> dict[str, Any]:
    result = _serve_status(app, app_status=app_status)
    dep = result["serve"]["apps"][app]["deployments"]["LLMServer:tiny-model"]
    dep.update(num_replicas_ready=ready, num_replicas_pending=pending, message=message)
    return result


async def _reconcile(dbsession: AsyncSession, dep: Any, status: dict[str, Any]) -> None:
    app = f"llmport-{dep.id}"
    fake = _FakeNodeControl(
        result_json=_healthy_probe_result(),
        results={
            NodeCommandType.RUN_SERVE_APP.value: _run_serve_result(app),
            NodeCommandType.GET_RAY_SERVE_STATUS.value: status,
        },
    )
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=fake)


def _scale(dep: Any, replicas: int) -> None:
    dep.spec_json = {**dep.spec_json, "scale": {"replicas": replicas}}
    dep.generation += 1


# ── through the reconciler ───────────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_scale_up_in_progress_is_serving_not_starting(deployment_env, dbsession: AsyncSession) -> None:
    dep, _node = deployment_env
    _scale(dep, 2)
    app = f"llmport-{dep.id}"

    await _reconcile(dbsession, dep, _status(app, app_status="DEPLOYING", ready=1, pending=1))

    assert dep.phase == "running", "one copy answers throughout"
    assert (dep.ready_replicas, dep.total_replicas) == (1, 2), "wanted is what was asked for"
    assert dep.phase_message == "Serving on 1 of 2 copies; 1 more starting."
    assert dep.observed_generation < dep.generation, "followed until it has both"


@pytest.mark.anyio()
async def test_it_settles_when_the_count_is_reached(deployment_env, dbsession: AsyncSession) -> None:
    dep, _node = deployment_env
    _scale(dep, 2)
    app = f"llmport-{dep.id}"

    await _reconcile(dbsession, dep, _status(app, app_status="RUNNING", ready=2, pending=0))

    assert dep.phase == "running"
    assert (dep.ready_replicas, dep.total_replicas) == (2, 2)
    assert dep.phase_message == "Serving on 2 copies."
    assert dep.observed_generation == dep.generation


@pytest.mark.anyio()
async def test_a_scale_down_is_followed_to_the_end(deployment_env, dbsession: AsyncSession) -> None:
    dep, _node = deployment_env
    _scale(dep, 1)
    app = f"llmport-{dep.id}"

    await _reconcile(dbsession, dep, _status(app, app_status="RUNNING", ready=2, pending=0))

    assert dep.phase == "running"
    assert dep.phase_message == "Serving on 2 copies; stopping 1 to reach 1."
    assert dep.observed_generation < dep.generation


@pytest.mark.anyio()
async def test_no_copy_yet_still_reads_as_starting(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from llm_port_backend.services.inference.drivers.ray import deployment as ray_deployment

    monkeypatch.setattr(ray_deployment, "_READINESS_PASS_BUDGET_SEC", 0.0)  # one probe
    dep, _node = deployment_env
    app = f"llmport-{dep.id}"

    await _reconcile(dbsession, dep, _status(app, app_status="DEPLOYING", ready=0, pending=1))

    assert dep.phase == "applying"
    assert "app application" not in (dep.phase_message or "")


# ── the words ────────────────────────────────────────────────────────────


def test_more_copies_than_accelerators_says_so_and_what_to_do() -> None:
    message = _scaling_message(
        ready=2, wanted=3, autoscaled=False, gpus_per_copy=1, cluster_gpus=2, app=None,
    )
    assert message == (
        "Serving on 2 of 3 copies. 1 cannot start: each copy needs 1 accelerator, and this "
        "cluster has 2, so 2 fit. Scale to 2, or add a machine to the cluster."
    )


def test_the_count_reached_but_ray_still_settling_reads_plainly() -> None:
    # Seen live: "Serving on 1 of 1 copies; 0 more starting."
    message = _scaling_message(
        ready=1, wanted=1, autoscaled=False, gpus_per_copy=1, cluster_gpus=2, app=None,
    )
    assert message == "Serving on 1 copy; finishing the change."


def test_a_copy_ray_cannot_place_is_explained() -> None:
    app = {"deployments": {"LLMServer:m": {
        "status": "UPDATING",
        "message": "Deployment 'LLMServer:m' has 1 replicas that have taken more than 30s to be scheduled.",
    }}}
    message = _scaling_message(
        ready=1, wanted=2, autoscaled=False, gpus_per_copy=1, cluster_gpus=2, app=app,
    )
    assert message.startswith("Serving on 1 of 2 copies; 1 more starting.")
    assert "not found room" in message


def test_the_first_copy_starting_reads_plainly() -> None:
    converged, reason = _app_deployment_readiness(
        {"status": "DEPLOYING", "message": "", "deployments": {}}, want_active=True,
    )
    assert not converged
    assert reason == "Starting the first copy."


# ── waking the reconciler ────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_committed_change_wakes_the_reconciler(dbsession: AsyncSession) -> None:
    wakeup.wake_reconciler_after_commit(dbsession)
    waiter = asyncio.create_task(wakeup.wait_for_work(5))
    await asyncio.sleep(0)
    await dbsession.commit()
    assert await waiter is True


@pytest.mark.anyio()
async def test_nothing_wakes_it_without_a_change() -> None:
    assert await wakeup.wait_for_work(0.05) is False
