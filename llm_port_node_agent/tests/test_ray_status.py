"""Enriched ``GET_RAY_STATUS`` / ``GET_RAY_SERVE_STATUS`` tests (SDK-First).

These cover the Dashboard-independent status probe built on the Ray Core
Python APIs, plus the §24 mandatory tests:

* ``GET_RAY_STATUS`` works with the dashboard disabled (core-tier flat fields
  + the tier structure);
* a **dead cluster** probe yields ``alive=False`` cleanly (no CLI involved);
* **CLI isolation** — the status path never shells out (``ray list`` /
  ``ray status`` / a bare ``ray --version``);
* a **metrics** tier failure must not fail health;
* a **state** tier that is Dashboard-dependent must report
  ``available=False`` without flipping ``alive``;
* **typed errors** (``RayError.code``) are stable and machine-readable;
* **idempotent attach** — repeated ``get_status`` does not re-
  init/shutdown the cluster or re-fetch the token;
* ``GET_RAY_SERVE_STATUS`` dispatches through the dispatcher and tolerates a
  missing app name.

The probe's internals are stubbed at the ``RayCoreClient.probe`` boundary
(the GCS round-trip) so these tests never touch a real cluster.
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
from llm_port_node_agent.models import NodeCommandType
from llm_port_node_agent.policy_guard import PolicyGuard
from llm_port_node_agent.runtime_manager import RuntimeManagerError
from llm_port_node_agent.state_store import StateStore
from llm_port_node_agent.ray import errors, models
from llm_port_node_agent.ray.core import RayCoreClient
from llm_port_node_agent.ray.manager import RayManager
from llm_port_node_agent.ray.metrics import RayMetricsDiscovery
from llm_port_node_agent.ray.models import (
    RayCapabilities,
    RayEnvironmentStatus,
    RayMetricsTargets,
    RayReplicaState,
    RayServeStatusTier,
    RayStateCapability,
)
from llm_port_node_agent.ray.serve import RayServeManager
from llm_port_node_agent.ray.state import RayStateDiagnostics


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _alive_env(**kw: Any) -> RayEnvironmentStatus:
    """A live single-head-node environment status (Tier A shape)."""
    return RayEnvironmentStatus(
        alive=True,
        version="2.58.0",
        num_nodes=kw.get("num_nodes", 1),
        nodes=kw.get("nodes")
        or [
            {
                "node_id": "n0",
                "ip": "10.0.0.1",
                "node_ip": "10.0.0.1",
                "node_manager_port": 10001,
                "state": "ALIVE",
                "is_head": True,
                "is_head_node": True,
                "gpus": 1.0,
                "cpus": 8.0,
                "ray_version": "2.58.0",
                "metrics_export_port": 63321,
            }
        ],
        resources=models.RayResourceTotals(cpu=8.0, gpu=1.0),
        available=models.RayAvailableResources(cpu=8.0, gpu=1.0),
        total_gpus=kw.get("total_gpus", 1.0),
        available_gpus=kw.get("available_gpus", 1.0),
        total_cpus=kw.get("total_cpus", 8.0),
        cluster_address=kw.get("cluster_address", "10.0.0.1:6379"),
        head_address=kw.get("head_address", "10.0.0.1"),
        capabilities=kw.get("capabilities") or RayCapabilities(cluster_sdk=True),
    )


def _dead_env() -> RayEnvironmentStatus:
    return RayEnvironmentStatus(alive=False)


class _FakeResponse:
    def __init__(self, json_payload: Any, status_code: int = 200) -> None:
        self._json = json_payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._json


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
        ray_base_path=str(tmp_path / "ray-base"),
        token_dir=tmp_path / "ray-tokens",
        http=MagicMock(),
    )


@pytest.fixture()
def stubbed_status(ray_manager: RayManager) -> dict[str, Any]:
    """Point every tier at a stubbed, controllable object."""
    core = MagicMock()
    core.probe = MagicMock(return_value=_alive_env())
    core.disconnect = MagicMock()
    ray_manager._core = core

    serve = MagicMock()
    serve.status = MagicMock(
        return_value=RayServeStatusTier(available=True, active=False, apps={})
    )
    ray_manager._serve = serve

    metrics = MagicMock()
    metrics.discover = MagicMock(
        return_value=RayMetricsTargets(
            enabled=True,
            targets=[
                {
                    "node_id": "n0",
                    "host": "10.0.0.1",
                    "port": 63321,
                    "url": "http://10.0.0.1:63321/metrics",
                    "labels": {"ray_node_id": "n0"},
                }
            ],
        )
    )
    ray_manager._metrics = metrics

    state_diag = MagicMock()
    state_diag.availability = MagicMock(
        return_value=RayStateCapability(available=False, detail="dashboard disabled")
    )
    ray_manager._state_diag = state_diag

    return {"core": core, "serve": serve, "metrics": metrics, "state": state_diag}


@pytest.fixture()
def dispatcher(ray_manager: RayManager, stubbed_status) -> CommandDispatcher:
    return CommandDispatcher(
        state_store=ray_manager._state,
        runtime_manager=MagicMock(),
        policy_guard=PolicyGuard(),
        events=EventBuffer(),
        ray_manager=ray_manager,
    )


def _cmd(command_type: str, command_id: str, payload: dict | None = None) -> dict:
    return {"id": command_id, "command_type": command_type, "payload": payload or {}}


# ---------------------------------------------------------------------------
# GET_RAY_STATUS — flat fields + tiers
# ---------------------------------------------------------------------------


def test_get_status_flat_and_tier_fields(ray_manager: RayManager, stubbed_status) -> None:
    """The enriched status keeps the flat keys (backend compat) and adds the
    tiers (additive-only, §19)."""
    import asyncio

    result = asyncio.run(ray_manager.get_status({}))
    # Flat fields the existing backend parser relies on.
    assert result["alive"] is True
    assert result["version"] == "2.58.0"
    assert result["num_nodes"] == 1
    assert result["nodes"][0]["is_head"] is True
    assert result["total_gpus"] == 1.0
    assert result["total_cpus"] == 8.0
    assert result["cluster_address"] == "10.0.0.1:6379"
    # Additive tiers.
    assert result["capabilities"]["cluster_sdk"] is True
    assert result["capabilities"]["serve"] is True
    assert result["serve"]["available"] is True
    assert result["metrics"] is None  # not requested by default
    assert result["state"] is None  # not requested by default


def test_get_status_includes_metrics_when_requested(
    ray_manager: RayManager, stubbed_status
) -> None:
    import asyncio

    result = asyncio.run(ray_manager.get_status({"include_metrics": True}))
    assert result["metrics"]["enabled"] is True
    assert result["metrics"]["targets"][0]["port"] == 63321
    assert result["capabilities"]["metrics"] is True


def test_get_status_includes_state_when_requested(ray_manager: RayManager, stubbed_status) -> None:
    import asyncio

    result = asyncio.run(ray_manager.get_status({"include_state": True}))
    assert result["state"]["available"] is False
    assert result["capabilities"]["state"] is False


def test_get_status_dead_cluster_returns_not_alive(ray_manager: RayManager) -> None:
    """A dead/absent cluster must be a *state* (alive=False) — the probe
    never raises out of the command path."""
    ray_manager._core = MagicMock(probe=MagicMock(return_value=_dead_env()))
    ray_manager._serve.status = MagicMock(return_value=RayServeStatusTier(available=False))
    import asyncio

    result = asyncio.run(ray_manager.get_status({}))
    assert result["alive"] is False
    assert result["num_nodes"] == 0
    assert result["nodes"] == []
    # Even with a dead core the command completes (success path), because
    # dead is a reported state, not an error.
    assert result["capabilities"]["cluster_sdk"] is False


def test_get_status_serve_tier_absent_when_core_dead(ray_manager: RayManager) -> None:
    ray_manager._core = MagicMock(probe=MagicMock(return_value=_dead_env()))
    ray_manager._serve.status = MagicMock(return_value=RayServeStatusTier(available=False))
    import asyncio

    result = asyncio.run(ray_manager.get_status({}))
    assert result["serve"]["available"] is False
    assert result["capabilities"]["serve"] is False


# ---------------------------------------------------------------------------
# RayCoreClient.probe: normalization + version-mismatch state
# ---------------------------------------------------------------------------


def _head_record() -> dict[str, Any]:
    return {
        "NodeID": "n0",
        "Alive": True,
        "NodeManagerAddress": "10.0.0.1",
        "NodeManagerHostname": "10.0.0.1",
        "NodeManagerPort": 10001,
        "ObjectManagerPort": 10002,
        "ObjectStoreSocketName": "",
        "RayletSocketName": "",
        "MetricsExportPort": 63321,
        "MetricsAgentPort": 1,
        "DashboardAgentListenPort": 0,
        "NodeName": "10.0.0.1",
        "RuntimeEnvAgentPort": 1,
        "DeathReason": "NONE",
        "DeathReasonMessage": "",
        "Resources": {
            "CPU": 8.0,
            "GPU": 2.0,
            "memory": 1.0,
            "object_store_memory": 1.0,
            "node:10.0.0.1": 1.0,
            "node:__internal_head__": 1.0,
            "accelerator_type:TITAN-RTX": 2.0,
        },
        "Labels": {},
    }


def test_probe_normalizes_head_and_resources() -> None:
    import ray as real_ray  # noqa: F401  (packaged with the agent)

    rec = _head_record()
    worker = dict(rec)
    worker["NodeID"] = "n1"
    worker["Alive"] = True
    worker["MetricsExportPort"] = 63322
    # Worker: no head resource key.
    worker["Resources"] = {k: v for k, v in rec["Resources"].items() if k != "node:__internal_head__"}

    class _Ray:
        __version__ = real_ray.__version__

        @staticmethod
        def is_initialized():
            return True

        @staticmethod
        def nodes():
            return [rec, worker]

        @staticmethod
        def cluster_resources():
            return {"CPU": 16.0, "GPU": 4.0, "memory": 2.0, "object_store_memory": 2.0}

        @staticmethod
        def available_resources():
            return {"CPU": 8.0, "GPU": 2.0}

        @staticmethod
        def get_runtime_context():
            rc = MagicMock()
            rc.gcs_address = "10.0.0.1:6379"
            return rc

    client = RayCoreClient(ray_module=_Ray())
    status = client.probe(expected_version=real_ray.__version__)
    assert status.alive is True
    assert status.num_nodes == 2
    assert status.head_address == "10.0.0.1"
    assert status.cluster_address == "10.0.0.1:6379"
    assert status.total_gpus == 4.0
    assert status.total_cpus == 16.0
    head = next(n for n in status.nodes if n["is_head"])
    assert head["metrics_export_port"] == 63321
    worker = next(n for n in status.nodes if not n["is_head"])
    assert worker["metrics_export_port"] == 63322


def test_probe_version_mismatch_alive_false(ray_manager: RayManager) -> None:
    """Intended != SDK version → typed RayVersionMismatchError inside probe,
    which folds to alive=False with the detail surfaced (Tier A unaffected)."""
    import ray as real_ray

    class _Ray:
        __version__ = real_ray.__version__

        @staticmethod
        def is_initialized():
            return True

        @staticmethod
        def nodes():
            return [_head_record()]

        @staticmethod
        def cluster_resources():
            return {}

        @staticmethod
        def available_resources():
            return {}

        @staticmethod
        def get_runtime_context():
            rc = MagicMock()
            rc.gcs_address = "10.0.0.1:6379"
            return rc

    client = RayCoreClient(ray_module=_Ray())
    status = client.probe(expected_version="9.9.9")
    assert status.alive is False
    # The detail is threaded through the version field for surfacing.
    assert "9.9.9" in (status.version or "")


def test_probe_attach_failure_alive_false(ray_manager: RayManager) -> None:
    class _Ray:
        __version__ = "2.58.0"

        @staticmethod
        def is_initialized():
            return False

        @staticmethod
        def init(*_a, **_kw):
            raise errors.RayAttachError("no local cluster", detail="address=auto found nothing")

    client = RayCoreClient(ray_module=_Ray())
    status = client.probe()
    assert status.alive is False
    assert status.num_nodes == 0


# ---------------------------------------------------------------------------
# Tier non-gating (metrics / state / serve must not fail health)
# ---------------------------------------------------------------------------


def test_metrics_failure_does_not_fail_health(ray_manager: RayManager, stubbed_status) -> None:
    """A metrics-tier failure (disabled/empty discovery) must not flip alive.
    RayMetricsDiscovery never raises; worst case it returns enabled=False."""
    import asyncio

    stubbed_status["metrics"].discover = MagicMock(return_value=RayMetricsTargets(enabled=False))
    result = asyncio.run(ray_manager.get_status({"include_metrics": True}))
    assert result["alive"] is True
    assert result["metrics"]["enabled"] is False
    assert result["capabilities"]["metrics"] is False


def test_state_dashboard_off_reports_unavailable(
    ray_manager: RayManager, stubbed_status
) -> None:
    """State is Dashboard-dependent: with the dashboard off, availability is
    ``available=False`` with a detail, and capabilities.state is False —
    while the cluster itself is alive (health never gates on Tier B)."""
    import asyncio

    stubbed_status["state"].availability = MagicMock(
        return_value=RayStateCapability(
            available=False, detail="State API unreachable (Dashboard component required)"
        )
    )
    result = asyncio.run(ray_manager.get_status({"include_state": True}))
    assert result["alive"] is True
    assert result["state"]["available"] is False
    assert result["capabilities"]["state"] is False
    assert "Dashboard" in (result["state"]["detail"] or "")


def test_serve_unavailable_does_not_fail_health(ray_manager: RayManager, stubbed_status) -> None:
    import asyncio

    stubbed_status["serve"].status = MagicMock(
        return_value=RayServeStatusTier(available=False, detail="no controller")
    )
    result = asyncio.run(ray_manager.get_status({}))
    assert result["alive"] is True
    assert result["serve"]["available"] is False
    assert result["capabilities"]["serve"] is False


# ---------------------------------------------------------------------------
# CLI isolation — the status path must never invoke the CLI
# ---------------------------------------------------------------------------


def test_status_does_not_invoke_cli(ray_manager: RayManager, stubbed_status, monkeypatch) -> None:
    """The core/metrics/state/serve tiers are SDK-only. Assert that
    RayRuntime._exec is never awaited during get_status (no subprocess for
    `ray list nodes` / `ray status` / `ray --version`)."""
    import asyncio

    calls: list[str] = []

    async def _forbid(*a: Any, **kw: Any) -> Any:
        calls.append(str(kw.get("label") or a))
        raise AssertionError(f"status path invoked the CLI: {a}")

    ray_manager._runtime._exec = _forbid  # type: ignore[assignment]
    ray_manager._process._exec = _forbid  # type: ignore[assignment]

    result = asyncio.run(
        ray_manager.get_status({"include_metrics": True, "include_state": True})
    )
    assert result["alive"] is True
    assert calls == []  # no CLI subprocess was spawned


# ---------------------------------------------------------------------------
# Typed error codes (§27)
# ---------------------------------------------------------------------------


def test_typed_error_codes_are_machine_readable() -> None:
    expected = {
        "ray_attach_failed": errors.RayAttachError,
        "ray_version_mismatch": errors.RayVersionMismatchError,
        "ray_runtime_error": errors.RayRuntimeError,
        "ray_serve_error": errors.RayServeError,
        "ray_metrics_discovery_failed": errors.RayMetricsDiscoveryError,
    }
    for code, cls in expected.items():
        assert cls.code == code
        # Every error carries a machine-readable code.
        assert isinstance(cls.code, str) and cls.code
        # report() is the surface the envelope uses.
        exc = cls("boom")
        report = exc.report
        assert report["code"] == code
        assert report["message"] == "boom"


def test_version_mismatch_is_a_kind_of_attach_error() -> None:
    # Inheritance: a version mismatch is a (kind of) attach failure at the
    # SDK layer, surfaced distinctly.
    assert issubclass(errors.RayVersionMismatchError, errors.RayAttachError)
    assert issubclass(errors.RayAttachError, errors.RayError)


# ---------------------------------------------------------------------------
# Idempotent attach (no re-init / no token re-fetch per probe)
# ---------------------------------------------------------------------------


def test_probe_idempotent_attach_no_reinit(ray_manager: RayManager) -> None:
    """Repeating the probe must not call ray.init again once attached."""
    import ray as real_ray

    init_calls: list[int] = []

    rec = _head_record()

    node_calls: list[int] = []

    class _Ray:
        __version__ = real_ray.__version__
        _initialized = True

        @staticmethod
        def is_initialized():
            return _Ray._initialized

        @staticmethod
        def init(*a, **kw):
            init_calls.append(1)
            _Ray._initialized = True

        @staticmethod
        def shutdown():
            pass

        @staticmethod
        def nodes():
            node_calls.append(1)
            return [rec]

        @staticmethod
        def cluster_resources():
            return {"CPU": 8.0, "GPU": 1.0}

        @staticmethod
        def available_resources():
            return {"CPU": 8.0, "GPU": 1.0}

        @staticmethod
        def get_runtime_context():
            rc = MagicMock()
            rc.gcs_address = "10.0.0.1:6379"
            return rc

    client = RayCoreClient(ray_module=_Ray())
    # Attach + probe twice.
    client.probe()
    client.probe()
    # Already initialized → no re-init, no re-shutdown per heartbeat…
    assert init_calls == []
    # …and a GCS round-trip still happens per probe (liveness re-verified):
    # one round-trip in ensure_attached() + one read of node state in probe().
    assert len(node_calls) == 4


def test_status_reuse_does_not_refetch_token(ray_manager: RayManager, stubbed_status) -> None:
    import asyncio

    ray_manager._http = MagicMock()
    ray_manager._http.get = AsyncMock(return_value=_FakeResponse({"token": "t"}))
    # Force a token write once (as start_head would).
    asyncio.run(ray_manager._write_token_securely({"credential_ref": "ref"}))
    ray_manager._http.reset_mock()

    # N status probes — must not re-fetch the token.
    for _ in range(3):
        asyncio.run(ray_manager.get_status({}))
    ray_manager._http.get.assert_not_awaited()


# ---------------------------------------------------------------------------
# serve.status() normalizer (unit, Dashboard-independent)
# ---------------------------------------------------------------------------


class _RawApp:
    def __init__(self) -> None:
        self.status = "RUNNING"
        self.message = ""
        self.last_deployed_time_s = 1.0
        self.deployments = {}


class _RawDep:
    def __init__(self) -> None:
        self.status = "RUNNING"
        self.status_trigger = None
        self.message = ""
        self.replica_states = {"READY": 2}


def test_serve_status_normalizes_apps_and_replicas(ray_manager: RayManager) -> None:
    serve = RayServeManager(core=MagicMock())
    raw = MagicMock()
    raw.applications = {"app-a": _RawApp()}
    raw.applications["app-a"].deployments["dep-a"] = _RawDep()

    with mock.patch.object(
        serve, "_serve_module", return_value=MagicMock(status=lambda: raw)
    ):
        serve._core.ensure_attached = MagicMock()
        tier = serve.status()

    assert tier.available is True
    assert tier.active is True
    app = tier.apps["app-a"]
    assert app.status == "RUNNING"
    dep = app.deployments["dep-a"]
    assert dep.num_replicas_ready == 2
    assert dep.num_replicas_pending == 0
    assert dep.replica_states == [RayReplicaState(state="READY", count=2)]


def test_serve_status_unavailable_when_no_modules(ray_manager: RayManager) -> None:
    serve = RayServeManager(core=MagicMock())
    serve._serve_module = lambda: None
    tier = serve.status()
    assert tier.available is False
    assert tier.detail is not None
    assert "import" in (tier.detail or "").lower()


# ---------------------------------------------------------------------------
# GET_RAY_SERVE_STATUS dispatch + app filtering
# ---------------------------------------------------------------------------


def test_get_serve_status_dispatch_success(dispatcher: CommandDispatcher, ray_manager: RayManager) -> None:
    import asyncio

    result = asyncio.run(dispatcher.handle(_cmd("get_ray_serve_status", "srv-1"), AsyncMock()))
    assert result["success"] is True
    assert result["result"]["alive"] is True
    assert result["result"]["serve"]["apps"] == {}


def test_get_serve_status_missing_app_empty(ray_manager: RayManager) -> None:
    import asyncio

    ray_manager._serve = MagicMock()
    ray_manager._serve.status = MagicMock(return_value=RayServeStatusTier(
        available=True, active=True,
        apps={"other": models.RayApplicationStatus(name="other", status="RUNNING")},
    ))
    result = asyncio.run(ray_manager.get_serve_status({"app_name": "nonexistent"}))
    assert result["alive"] is True
    assert result["serve"]["apps"] == {}


def test_get_serve_status_named_app_returned(ray_manager: RayManager) -> None:
    import asyncio

    app = models.RayApplicationStatus(name="other", status="RUNNING")
    ray_manager._serve = MagicMock()
    ray_manager._serve.status = MagicMock(
        return_value=RayServeStatusTier(available=True, active=True, apps={"other": app})
    )
    result = asyncio.run(ray_manager.get_serve_status({"app_name": "other"}))
    assert result["alive"] is True
    assert "other" in result["serve"]["apps"]
    assert set(result["serve"]["apps"].keys()) == {"other"}


def test_get_serve_status_alive_false_when_serve_down(
    ray_manager: RayManager, stubbed_status
) -> None:
    import asyncio

    stubbed_status["serve"].status = MagicMock(return_value=RayServeStatusTier(available=False))
    result = asyncio.run(ray_manager.get_serve_status({}))
    assert result["alive"] is False


def test_dispatcher_serve_status_without_manager_failure(state: StateStore) -> None:
    import asyncio

    d = CommandDispatcher(
        state_store=state,
        runtime_manager=MagicMock(),
        policy_guard=PolicyGuard(),
        events=EventBuffer(),
        ray_manager=None,
    )
    result = asyncio.run(d.handle(_cmd("get_ray_serve_status", "srv-none"), AsyncMock()))
    assert result["success"] is False
    assert result["error_code"] == "runtime_error"


# ---------------------------------------------------------------------------
# RayServeStatusResult / RayStatusResult schemas (additive §19 surface)
# ---------------------------------------------------------------------------


def test_ray_status_result_projection_from_environment_status() -> None:
    from llm_port_node_agent.ray.schemas import RayStatusResult

    flat = RayStatusResult.from_environment_status(_alive_env())
    assert flat.alive is True
    assert flat.version == "2.58.0"
    assert flat.num_nodes == 1
    assert flat.total_gpus == 1.0
    assert flat.cluster_address == "10.0.0.1:6379"


def test_serve_status_result_json_roundtrip() -> None:
    """The GET_RAY_SERVE_STATUS result must be a plain-JSON document (no Ray
    objects leak across the wire)."""
    from llm_port_node_agent.ray.schemas import RayServeStatusResult

    res = RayServeStatusResult(
        alive=True,
        serve={
            "available": True,
            "active": True,
            "detail": None,
            "apps": {"a": {"name": "a", "status": "RUNNING", "message": "", "deployments": {}}},
        },
    )
    dumped = json.loads(json.dumps(res.model_dump(mode="json")))
    assert dumped["alive"] is True
    assert dumped["serve"]["apps"]["a"]["status"] == "RUNNING"
