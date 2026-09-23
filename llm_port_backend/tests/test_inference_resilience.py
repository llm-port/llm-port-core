"""Phase 7: a cluster that was up is watched, and brought back when it is not.

Before this, a cluster was never looked at again once it read "ready", and a
deployment never again once it read "running". A Ray head that died left both
as they were -- with every request failing -- until someone changed
something. These tests pin the recovery behaviour down scenario by scenario,
with the node side scripted; the hardware runs are in
``docs/resilience.md``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.inference_dao import DeploymentDAO
from llm_port_backend.db.models.inference import (
    EnvironmentStatus,
    InferenceDeployment,
    InferenceEnvironment,
)
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.db.models.node_control import NodeCommandType
from tests.test_inference_ray import (  # noqa: F401 - fixtures are used by name
    _deployment_spec,
    _FakeNodeControl,
    _fast_readiness,
    _ray_driver,
    _run_serve_result,
    _seed_environment,
    _serve_status,
    deployment_env,
    probe_env,
)

STATUS = NodeCommandType.GET_RAY_STATUS.value
SERVE = NodeCommandType.GET_RAY_SERVE_STATUS.value
RUN = NodeCommandType.RUN_SERVE_APP.value
STOP = NodeCommandType.STOP_RAY.value
LEAVE = NodeCommandType.LEAVE_RAY_CLUSTER.value
START = NodeCommandType.START_RAY_HEAD.value
JOIN = NodeCommandType.JOIN_RAY_CLUSTER.value

HEAD_IP, WORKER_IP = "10.0.0.1", "10.0.0.2"

#: What the agent answers when Ray on the head is gone: the helper could not
#: attach, or timed out trying.
DEAD: dict[str, Any] = {"alive": False, "version": None, "num_nodes": 0, "nodes": []}


def _probe(*alive: str, head_id: str = "head-A") -> dict[str, Any]:
    """A cluster answering with *alive* as its live members."""
    nodes = [
        {
            "node_id": head_id if ip == HEAD_IP else f"ray-{ip}",
            "node_ip": ip,
            "alive": True,
            "is_head": ip == HEAD_IP,
        }
        for ip in alive
    ]
    return {
        "alive": True,
        "version": "2.58.0",
        "num_nodes": len(nodes),
        "nodes": nodes,
        "total_gpus": float(len(nodes)),
        "cluster_address": f"{HEAD_IP}:6379",
    }


class _Scripted(_FakeNodeControl):
    """Answers each command type from a list, in order; the last answer repeats."""

    def __init__(self, script: dict[str, list[dict[str, Any] | None]]) -> None:
        super().__init__(result_json=None)
        self._script = {kind: list(answers) for kind, answers in script.items()}

    def _result_for(self, command_type: str) -> dict[str, Any] | None:
        answers = self._script.get(command_type)
        if answers:
            return answers.pop(0) if len(answers) > 1 else answers[0]
        return super()._result_for(command_type)

    @property
    def types(self) -> list[str]:
        return [c["command_type"] for c in self.issued]


@pytest.fixture()
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """No waiting between looks: the order of events is what is under test."""
    from llm_port_backend.services.inference.drivers.ray import environment

    monkeypatch.setattr(environment, "_CONFIRM_AFTER_SEC", 0)
    monkeypatch.setattr(environment, "_RECOVERY_BACKOFF_SEC", 0)
    monkeypatch.setattr(environment, "_SETTLE_SEC", 0.0)
    monkeypatch.setattr(environment, "_SETTLE_POLL_SEC", 0.0)


def _up(env: InferenceEnvironment, *, head_id: str = "head-A") -> None:
    """A cluster that came up at its generation and was last seen whole."""
    env.status = EnvironmentStatus.READY.value
    env.observed_generation = env.generation
    env.observed_status_json = {
        "converged_generation": env.generation,
        "cluster": _probe(HEAD_IP, WORKER_IP, head_id=head_id),
    }


async def _pass(dbsession: AsyncSession, env: InferenceEnvironment, fake: _FakeNodeControl) -> None:
    await _ray_driver().environment_manager.reconcile_environment(dbsession, env, node_control=fake)


# ---------------------------------------------------------------------------
# The cluster
# ---------------------------------------------------------------------------


async def test_a_cluster_that_is_up_is_looked_at_not_started_again(
    probe_env, dbsession: AsyncSession, quick: None,
) -> None:
    _cp, env, _nodes = probe_env
    _up(env)
    fake = _Scripted({STATUS: [_probe(HEAD_IP, WORKER_IP)]})

    await _pass(dbsession, env, fake)

    assert fake.types == [STATUS], "one probe, and nothing issued to a healthy cluster"
    assert env.status == EnvironmentStatus.READY
    assert env.observed_generation == env.generation


async def test_the_start_sequence_marks_the_cluster_as_up(
    probe_env, dbsession: AsyncSession, quick: None,
) -> None:
    """Once started, later passes are health checks rather than another start."""
    from llm_port_backend.services.inference.service import _queue_for_reconcile

    _cp, env, _nodes = probe_env
    fake = _Scripted({STATUS: [_probe(HEAD_IP, WORKER_IP)]})
    await _pass(dbsession, env, fake)
    assert START in fake.types
    assert env.observed_status_json["converged_generation"] == env.generation

    _queue_for_reconcile(env)
    again = _Scripted({STATUS: [_probe(HEAD_IP, WORKER_IP)]})
    await _pass(dbsession, env, again)
    assert again.types == [STATUS]


async def test_one_failed_look_restarts_nothing(
    probe_env, dbsession: AsyncSession, quick: None,
) -> None:
    """A probe that timed out on a busy head is not a dead cluster."""
    _cp, env, nodes = probe_env
    _up(env)
    last_seen = dict(env.observed_status_json["cluster"])
    fake = _Scripted({STATUS: [DEAD]})

    await _pass(dbsession, env, fake)

    assert fake.types == [STATUS]
    assert env.status == EnvironmentStatus.DEGRADED
    assert env.status_message == (
        f"Ray on {nodes[0].agent_id}, the cluster's head, is not running. "
        "Checking again before restarting anything."
    )
    # The models' users are not told on one look: the snapshot the deployments
    # read is left as it was.
    assert env.observed_status_json["cluster"] == last_seen


async def test_a_second_look_that_finds_it_healthy_is_a_false_alarm(
    probe_env, dbsession: AsyncSession, quick: None,
) -> None:
    _cp, env, _nodes = probe_env
    _up(env)
    fake = _Scripted({STATUS: [DEAD, _probe(HEAD_IP, WORKER_IP)]})

    await _pass(dbsession, env, fake)
    await _pass(dbsession, env, fake)

    assert fake.types == [STATUS, STATUS]
    assert env.status == EnvironmentStatus.READY
    assert env.status_message is None
    assert "recovery" not in env.observed_status_json
    assert "last_recovery" not in env.observed_status_json


async def test_a_lost_head_is_reformed_from_clean(
    probe_env, dbsession: AsyncSession, quick: None,
) -> None:
    _cp, env, nodes = probe_env
    head, worker = nodes
    _up(env)
    fake = _Scripted({STATUS: [DEAD, DEAD, _probe(HEAD_IP, WORKER_IP, head_id="head-B")]})

    await _pass(dbsession, env, fake)  # first look
    await _pass(dbsession, env, fake)  # second look, and the restart

    # Stop everything first -- the agent's start is a no-op on a node that
    # still thinks it heads the cluster -- then start the head and join.
    assert fake.types == [STATUS, STATUS, LEAVE, STOP, START, JOIN, STATUS]
    stop = fake.by_type(STOP)[0]
    assert stop["node_id"] == head.id and stop["payload"]["force"] is True
    assert fake.by_type(LEAVE)[0]["node_id"] == worker.id
    assert fake.by_type(START)[0]["node_id"] == head.id
    join = fake.by_type(JOIN)[0]
    assert join["node_id"] == worker.id and join["payload"]["head_address"] == f"{HEAD_IP}:6379"
    # Its own keys: a restart never resumes, or is mistaken for, the start.
    assert all(":recover1:" in c["idempotency_key"] for c in fake.issued if c["command_type"] != STATUS)

    assert env.status == EnvironmentStatus.READY
    assert env.status_message is None
    assert "recovery" not in env.observed_status_json
    record = env.observed_status_json["last_recovery"]
    assert record["kind"] == "head" and record["attempts"] == 1
    # The new head is what the deployments will see and re-apply on.
    assert env.observed_status_json["cluster"]["nodes"][0]["node_id"] == "head-B"


async def test_a_lost_worker_is_rejoined_and_the_head_left_alone(
    probe_env, dbsession: AsyncSession, quick: None,
) -> None:
    _cp, env, nodes = probe_env
    _head, worker = nodes
    _up(env)
    fake = _Scripted({STATUS: [_probe(HEAD_IP), _probe(HEAD_IP), _probe(HEAD_IP, WORKER_IP)]})

    await _pass(dbsession, env, fake)
    assert env.status_message == (
        f"{worker.agent_id} has dropped out of the cluster. Checking again before restarting anything."
    )
    await _pass(dbsession, env, fake)

    assert fake.types == [STATUS, STATUS, LEAVE, JOIN, STATUS]
    assert {c["node_id"] for c in fake.issued if c["command_type"] in (LEAVE, JOIN)} == {worker.id}
    assert env.status == EnvironmentStatus.READY
    assert env.observed_status_json["last_recovery"]["kind"] == "members"


async def test_recovery_gives_up_after_three_attempts_and_says_what_it_tried(
    probe_env, dbsession: AsyncSession, quick: None,
) -> None:
    from llm_port_backend.services.inference.service import EnvironmentService

    _cp, env, nodes = probe_env
    _up(env)
    fake = _Scripted({STATUS: [DEAD]})

    await _pass(dbsession, env, fake)  # first look
    await _pass(dbsession, env, fake)  # attempt 1
    assert env.status == EnvironmentStatus.DEGRADED
    assert "Restart 1 of 3 did not bring it back" in env.status_message
    await _pass(dbsession, env, fake)  # attempt 2
    await _pass(dbsession, env, fake)  # attempt 3

    assert fake.types.count(START) == 3
    assert env.status == EnvironmentStatus.FAILED
    assert env.status_message.startswith(f"Ray on {nodes[0].agent_id}, the cluster's head, is not running.")
    assert "restarted it 3 times" in env.status_message
    assert "tries again every 15 minutes; use Try again" in env.status_message

    # An explicit failure, and a quiet one: nothing more until someone asks.
    issued = len(fake.issued)
    await _pass(dbsession, env, fake)
    assert len(fake.issued) == issued

    # Try again starts over, from a fresh look.
    await EnvironmentService(dbsession).request_reconcile(env.id)
    await _pass(dbsession, env, fake)
    assert fake.types[issued:] == [STATUS]
    assert env.status == EnvironmentStatus.DEGRADED
    assert "Checking again" in env.status_message


async def test_a_cluster_given_up_on_is_still_looked_at_now_and_then(
    probe_env, dbsession: AsyncSession, quick: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partition that heals, a machine repaired: nobody tells LLM.Port."""
    from llm_port_backend.services.inference.drivers.ray import environment

    _cp, env, _nodes = probe_env
    _up(env)
    # One look, then three restarts of two looks each (before, after); then
    # the late re-look and its restart; then it is back.
    fake = _Scripted({STATUS: [DEAD] * 9 + [_probe(HEAD_IP, WORKER_IP)]})
    for _ in range(4):
        await _pass(dbsession, env, fake)
    assert env.status == EnvironmentStatus.FAILED

    monkeypatch.setattr(environment, "_GAVE_UP_RECHECK_SEC", 0)
    env.observed_status_json = {
        **env.observed_status_json,
        "recovery": {**env.observed_status_json["recovery"], "next_look_at": None},
    }
    await _pass(dbsession, env, fake)  # still down: one more restart, then back to waiting
    assert fake.types.count(START) == 4
    assert env.status == EnvironmentStatus.FAILED
    assert "restarted it 4 times" in env.status_message

    await _pass(dbsession, env, fake)  # back by now
    assert env.status == EnvironmentStatus.READY
    assert env.observed_status_json["last_recovery"]["attempts"] == 4


async def test_a_worker_machine_going_offline_keeps_what_the_head_last_said(
    probe_env, dbsession: AsyncSession, quick: None,
) -> None:
    """The head can still be asked, so its models can still report their
    surviving copies; only a missing head makes the cluster unknown."""
    from datetime import UTC, datetime, timedelta

    _cp, env, nodes = probe_env
    _up(env)
    last_seen = dict(env.observed_status_json["cluster"])
    nodes[1].status = "offline"
    nodes[1].last_seen = datetime.now(tz=UTC) - timedelta(minutes=10)
    await dbsession.flush()
    fake = _Scripted({})

    await _pass(dbsession, env, fake)

    assert fake.issued == []
    assert env.status == EnvironmentStatus.DEGRADED
    assert env.status_message.startswith(f"{nodes[1].agent_id} is offline")
    assert env.observed_status_json["cluster"] == last_seen

    nodes[0].status = "offline"
    nodes[0].last_seen = datetime.now(tz=UTC) - timedelta(minutes=10)
    await dbsession.flush()
    await _pass(dbsession, env, fake)
    assert env.status == EnvironmentStatus.FAILED
    assert env.observed_status_json["cluster"]["observed"] is False


async def test_one_machine_dropping_on_its_own_is_not_hidden_for_two_minutes(
    probe_env, dbsession: AsyncSession, quick: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The long grace is for a backend restart, when every agent drops at once.

    On the DGX pair the head rebooted and its cluster read "ready" for two and
    a half minutes, the grace covering for a machine that was simply down.
    """
    import time
    from datetime import UTC, datetime, timedelta

    from llm_port_backend.services.inference.drivers.ray import environment

    _cp, env, nodes = probe_env
    _up(env)
    nodes[0].status = "offline"
    nodes[0].last_seen = datetime.now(tz=UTC) - timedelta(seconds=60)
    await dbsession.flush()

    monkeypatch.setattr(environment, "_PROCESS_STARTED", time.monotonic())
    await _pass(dbsession, env, _Scripted({}))
    assert env.status == EnvironmentStatus.READY, "just after a backend start: reconnecting"

    monkeypatch.setattr(environment, "_PROCESS_STARTED", time.monotonic() - 3600)
    await _pass(dbsession, env, _Scripted({}))
    assert env.status == EnvironmentStatus.FAILED
    assert env.status_message.startswith(f"{nodes[0].agent_id} is offline")


async def test_a_probe_nobody_answered_restarts_nothing(
    probe_env, dbsession: AsyncSession, quick: None,
) -> None:
    """No answer from the head's agent is not an answer about Ray."""
    _cp, env, _nodes = probe_env
    _up(env)
    fake = _Scripted({STATUS: [None]})

    await _pass(dbsession, env, fake)

    assert fake.types == [STATUS]
    assert env.status == EnvironmentStatus.READY
    assert "recovery" not in env.observed_status_json


# ---------------------------------------------------------------------------
# The models on it
# ---------------------------------------------------------------------------


async def _cluster(
    dbsession: AsyncSession, dep: InferenceDeployment, *, status: str, message: str | None, cluster: dict,
) -> InferenceEnvironment:
    env = await dbsession.get(InferenceEnvironment, dep.environment_id)
    env.status = status
    env.status_message = message
    env.observed_status_json = {"cluster": cluster}
    await dbsession.flush()
    return env


def _serving(dep: InferenceDeployment, *, ready: int, wanted: int | None = None) -> None:
    dep.phase = "running"
    dep.ready_replicas = ready
    dep.total_replicas = wanted or ready
    dep.observed_generation = dep.generation


async def _deploy(dbsession: AsyncSession, dep: InferenceDeployment, fake: _FakeNodeControl) -> None:
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=fake)


async def test_a_model_on_a_cluster_whose_head_is_down_reads_not_serving(
    deployment_env, dbsession: AsyncSession,
) -> None:
    dep, _node = deployment_env
    _serving(dep, ready=2)
    await _cluster(
        dbsession, dep,
        status=EnvironmentStatus.DEGRADED.value,
        message="Ray on spark-3201, the cluster's head, is not running. Restarting the cluster (attempt 1 of 3).",
        cluster={"alive": False, "observed": True},
    )
    fake = _Scripted({})

    await _deploy(dbsession, dep, fake)

    assert fake.issued == [], "nothing to ask a cluster whose head is down"
    assert dep.phase == "degraded"
    assert dep.ready_replicas == 0, "zero stops the gateway routing to it"
    assert dep.phase_message.startswith("Not serving. Ray on spark-3201, the cluster's head, is not running.")
    await dbsession.flush()
    queued = await DeploymentDAO(dbsession).list_pending_observation()
    assert dep.id in {d.id for d in queued}, "stays queued until the cluster is back"


async def test_a_surviving_copy_keeps_serving_while_a_worker_is_rejoined(
    deployment_env, dbsession: AsyncSession,
) -> None:
    dep, _node = deployment_env
    dep.spec_json = _deployment_spec(replicas=2)
    _serving(dep, ready=2)
    await _cluster(
        dbsession, dep,
        status=EnvironmentStatus.DEGRADED.value,
        message="spark-ts3202 has dropped out of the cluster. Rejoining (attempt 1 of 3).",
        cluster=_probe(HEAD_IP),
    )
    app = f"llmport-{dep.id}"
    fake = _Scripted({SERVE: [_serve_status(app)]})

    await _deploy(dbsession, dep, fake)

    assert RUN not in fake.types, "nothing is applied to a cluster that is not ready"
    assert dep.phase == "degraded"
    assert dep.ready_replicas == 1 and dep.total_replicas == 2
    assert dep.phase_message.startswith("Serving on 1 of 2 copies. spark-ts3202 has dropped out")


async def test_a_model_that_cannot_be_checked_keeps_its_counts(
    deployment_env, dbsession: AsyncSession,
) -> None:
    """The head machine is offline: nothing is known, so nothing is claimed."""
    dep, _node = deployment_env
    _serving(dep, ready=2)
    await _cluster(
        dbsession, dep,
        status=EnvironmentStatus.FAILED.value,
        message="spark-3201 is offline: no agent connected.",
        cluster={"alive": False, "observed": False},
    )
    fake = _Scripted({})

    await _deploy(dbsession, dep, fake)

    assert dep.phase == "degraded"
    assert dep.ready_replicas == 2
    assert dep.phase_message.startswith("Cannot check the model right now. spark-3201 is offline")


async def test_a_model_is_reapplied_when_the_cluster_has_a_new_head(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_readiness(monkeypatch)
    dep, _node = deployment_env
    app = f"llmport-{dep.id}"
    env = await _cluster(
        dbsession, dep, status=EnvironmentStatus.READY.value, message=None,
        cluster=_probe(HEAD_IP, head_id="head-A"),
    )
    first = _Scripted({RUN: [_run_serve_result(app)], SERVE: [_serve_status(app)]})
    await _deploy(dbsession, dep, first)
    assert dep.phase == "running"
    assert dep.observed_status_json["applied_head"] == "head-A"

    # The head was re-formed. The new one lists the app as missing only if
    # asked the right way; the head's identity says so without asking.
    env.observed_status_json = {"cluster": _probe(HEAD_IP, head_id="head-B")}
    dep.phase = "degraded"
    said: list[tuple[str, str, int]] = []

    async def progress(session: Any) -> None:
        said.append((dep.phase, dep.phase_message, dep.ready_replicas))

    from llm_port_backend.services.inference.drivers.ray.deployment import RayDeploymentManager

    monkeypatch.setattr(RayDeploymentManager, "_commit_progress", staticmethod(progress))
    again = _Scripted({RUN: [_run_serve_result(app)], SERVE: [_serve_status(app)]})
    await _deploy(dbsession, dep, again)

    assert again.types.count(RUN) == 1
    # Said while the copies start, not after: the wait takes minutes.
    assert said == [(
        "applying", "The cluster was restarted: applying the model again; its copies are starting.", 0,
    )]
    assert dep.phase == "running"
    assert dep.observed_status_json["applied_head"] == "head-B"


async def test_the_head_is_recorded_for_a_model_applied_before_it_was_kept(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_readiness(monkeypatch)
    dep, _node = deployment_env
    app = f"llmport-{dep.id}"
    await _cluster(
        dbsession, dep, status=EnvironmentStatus.READY.value, message=None,
        cluster=_probe(HEAD_IP, head_id="head-A"),
    )
    await _deploy(dbsession, dep, _Scripted({RUN: [_run_serve_result(app)], SERVE: [_serve_status(app)]}))
    observed = dict(dep.observed_status_json)
    observed.pop("applied_head")
    dep.observed_status_json = observed

    fake = _Scripted({SERVE: [_serve_status(app)]})
    await _deploy(dbsession, dep, fake)

    assert RUN not in fake.types, "recording it is no reason to restart the model"
    assert dep.observed_status_json["applied_head"] == "head-A"


async def test_a_look_at_a_serving_model_never_recompiles_it(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What happened on the DGX pair the first time the health check ran.

    The look came while the agents were reconnecting; the model read as not
    on the machines, the config compiled to the remote source instead of the
    local copy, its hash changed, and the serving model was re-applied --
    both copies restarted.
    """
    from llm_port_backend.services.inference.drivers.ray.deployment import RayDeploymentManager

    _fast_readiness(monkeypatch)
    dep, _node = deployment_env
    app = f"llmport-{dep.id}"
    await _deploy(dbsession, dep, _Scripted({RUN: [_run_serve_result(app)], SERVE: [_serve_status(app)]}))
    assert dep.observed_status_json["applied_generation"] == dep.generation

    def compiles_differently(self: Any, facts: Any) -> dict[str, Any]:
        raise AssertionError("a look must not compile")

    monkeypatch.setattr(RayDeploymentManager, "_compile", compiles_differently)
    fake = _Scripted({SERVE: [_serve_status(app)]})
    await _deploy(dbsession, dep, fake)

    assert RUN not in fake.types
    assert dep.phase == "running"


async def test_nothing_is_applied_while_a_machine_is_reconnecting(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The root of the restarts: an offline member reads as not having the
    model, and the config compiled to the remote source."""
    from llm_port_backend.services.inference.drivers.ray.deployment import RayDeploymentManager

    _fast_readiness(monkeypatch)
    dep, node = deployment_env
    node.status = "offline"
    await dbsession.flush()

    def must_not_compile(self: Any, facts: Any) -> dict[str, Any]:
        raise AssertionError("compiled against a machine that is away")

    monkeypatch.setattr(RayDeploymentManager, "_compile", must_not_compile)
    fake = _Scripted({})
    await _deploy(dbsession, dep, fake)

    assert fake.issued == []
    assert dep.phase == "pending"
    assert dep.phase_message == f"Waiting for {node.agent_id} to reconnect before applying."


async def test_a_look_nobody_answered_leaves_a_serving_model_as_it_was(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_readiness(monkeypatch)
    dep, _node = deployment_env
    app = f"llmport-{dep.id}"
    await _deploy(dbsession, dep, _Scripted({RUN: [_run_serve_result(app)], SERVE: [_serve_status(app)]}))

    fake = _Scripted({SERVE: [None]})
    await _deploy(dbsession, dep, fake)

    assert RUN not in fake.types
    assert dep.phase == "running", "not 'applying': nothing was applied"
    assert dep.ready_replicas == 1
    assert dep.phase_message.startswith("Could not check the model just now")


async def test_a_cluster_with_no_serve_at_all_gets_the_model_applied(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a freshly started head answers: absent, not unobserved."""
    _fast_readiness(monkeypatch)
    dep, _node = deployment_env
    app = f"llmport-{dep.id}"
    await _deploy(dbsession, dep, _Scripted({RUN: [_run_serve_result(app)], SERVE: [_serve_status(app)]}))
    assert dep.phase == "running"

    no_serve = {"available": False, "apps": {}, "detail": "There is no Serve instance running on this Ray cluster."}
    # Both looks before the apply find no Serve; the one after finds the app.
    fake = _Scripted({RUN: [_run_serve_result(app)], SERVE: [no_serve, no_serve, _serve_status(app)]})
    await _deploy(dbsession, dep, fake)

    assert fake.types.count(RUN) == 1
    assert dep.phase == "running"


# ---------------------------------------------------------------------------
# The reconciler around them
# ---------------------------------------------------------------------------


async def _deployment_on(dbsession: AsyncSession, env: InferenceEnvironment, **fields: Any) -> InferenceDeployment:
    model = LLMModel(
        display_name=f"org/m-{uuid.uuid4().hex[:6]}",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="org/m",
    )
    dbsession.add(model)
    await dbsession.flush()
    dep = InferenceDeployment(
        environment_id=env.id, model_id=model.id, name=f"dep-{uuid.uuid4().hex[:12]}",
        spec_json=_deployment_spec(), **fields,
    )
    dbsession.add(dep)
    await dbsession.flush()
    dep.observed_generation = dep.generation
    await dbsession.flush()
    return dep


async def test_a_cluster_going_down_or_coming_back_queues_its_models(
    probe_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from llm_port_backend.services.inference import reconciliation
    from llm_port_backend.services.inference.drivers.ray.environment import RayEnvironmentManager

    _cp, env, _nodes = probe_env
    env.status = EnvironmentStatus.READY.value
    serving = await _deployment_on(dbsession, env, phase="running")
    stopped = await _deployment_on(dbsession, env, phase="stopped", desired_state="stopped")
    _ray_driver()  # registers "ray"

    async def goes_down(self: Any, session: Any, environment: Any, **_kw: Any) -> None:
        environment.status = EnvironmentStatus.DEGRADED.value

    monkeypatch.setattr(RayEnvironmentManager, "reconcile_environment", goes_down)
    context = reconciliation.ReconciliationContext.for_session(dbsession)
    await reconciliation.reconcile_environment(context, env)

    assert serving.observed_generation == serving.generation - 1
    assert stopped.observed_generation == stopped.generation, "a stopped model has nothing to find out"

    # No change, no queueing: a health check that finds it as it was is quiet.
    serving.observed_generation = serving.generation
    await reconciliation.reconcile_environment(context, env)
    assert serving.observed_generation == serving.generation


async def test_health_checks_queue_what_is_up_and_nothing_else(dbsession: AsyncSession) -> None:
    from llm_port_backend.web.lifespan import _queue_health_checks

    ready = await _seed_environment(dbsession, generation=2, observed_generation=2)
    failed = await _seed_environment(
        dbsession, generation=2, observed_generation=2, status=EnvironmentStatus.FAILED.value,
    )
    stopping = await _seed_environment(dbsession, generation=2, observed_generation=2)
    stopping.desired_state = "stopped"
    running = await _deployment_on(dbsession, ready, phase="running")
    pending = await _deployment_on(dbsession, ready, phase="pending")
    await dbsession.flush()

    await _queue_health_checks(dbsession)

    assert ready.observed_generation == ready.generation - 1
    assert running.observed_generation == running.generation - 1
    # Already in the queue on its own, or not meant to be running.
    assert failed.observed_generation == failed.generation
    assert stopping.observed_generation == stopping.generation
    assert pending.observed_generation == pending.generation


def test_health_checks_come_round_at_their_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_port_backend.web import lifespan

    now = [1000.0]
    monkeypatch.setattr(lifespan.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(lifespan, "_last_health_check", None)
    monkeypatch.setattr(lifespan.settings, "inference_health_check_sec", 60)

    assert lifespan._health_check_due(), "due at once after a restart"
    now[0] += 30
    assert not lifespan._health_check_due()
    now[0] += 31
    assert lifespan._health_check_due()

    monkeypatch.setattr(lifespan.settings, "inference_health_check_sec", 0)
    now[0] += 3600
    assert not lifespan._health_check_due(), "0 turns them off"


def test_the_reason_serve_is_missing_survives_parsing() -> None:
    from llm_port_backend.services.inference.drivers.ray.client import _parse_serve_status

    helper_shape = {"available": False, "applications": {}, "error": "There is no Serve instance running."}
    assert _parse_serve_status(helper_shape).detail == "There is no Serve instance running."
