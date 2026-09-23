"""Integration tests for the /api/inference CRUD + desired-state endpoints.

These run against the shared test Postgres (see ``tests/conftest.py``) with a
superuser injected via ``current_active_user`` (superusers bypass RBAC; the
RBAC matrix itself is covered in ``test_inference_rbac.py``).

Every test rolls back through the per-test savepoint, so no state leaks.

Enum note: the inference models persist StrEnum *values* (lowercase, e.g.
``pending`` / ``running`` / ``active``), and DTOs return those values verbatim.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.db.models.users import User, current_active_user

API = "/api/inference"
API_VERSION = "inference.llmport.ai/v1alpha1"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_superuser() -> User:
    """A superuser bypasses RBAC; only id / flags are read by the deps."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = True
    user.is_verified = True
    return user


@pytest.fixture()
def superuser() -> User:
    return _make_superuser()


@pytest.fixture()
def authed_fapp(fastapi_app: FastAPI, superuser: User) -> FastAPI:
    """The conftest app with the current user overridden to a superuser."""
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    return fastapi_app


async def make_control_plane(client: AsyncClient, name: str = "cp", driver: str = "ray") -> dict:
    r = await client.post(f"{API}/control-planes", json={"name": name, "driver": driver})
    assert r.status_code == 201, r.text
    return r.json()


async def make_environment(client: AsyncClient, control_plane_id, name: str = "env") -> dict:
    r = await client.post(
        f"{API}/environments",
        json={"control_plane_id": str(control_plane_id), "name": name},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def make_model(dbsession: AsyncSession) -> uuid.UUID:
    model = LLMModel(
        display_name="Model",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
    )
    dbsession.add(model)
    await dbsession.flush()
    return model.id


async def make_node(dbsession: AsyncSession) -> uuid.UUID:
    node = InfraNode(agent_id=f"agent-{uuid.uuid4().hex}", host="node-host")
    dbsession.add(node)
    await dbsession.flush()
    return node.id


def good_spec(**overrides: Any) -> dict:
    return {"api_version": API_VERSION, "scale": {"replicas": 1}, **overrides}


async def _seed_env_and_model(
    client: AsyncClient, dbsession: AsyncSession, cp_name: str, env_name: str
) -> tuple[dict, uuid.UUID]:
    cp = await make_control_plane(client, name=cp_name)
    env = await make_environment(client, cp["id"], name=env_name)
    model_id = await make_model(dbsession)
    return env, model_id


async def _create_deploy(
    client: AsyncClient,
    env: dict,
    model_id: uuid.UUID,
    name: str,
    spec: dict | None = None,
) -> dict:
    r = await client.post(
        f"{API}/deployments",
        json={
            "environment_id": str(env["id"]),
            "model_id": str(model_id),
            "name": name,
            "spec": spec or good_spec(),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Control planes
# ---------------------------------------------------------------------------


async def test_control_plane_create_and_get(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp-a")
    assert cp["name"] == "cp-a"
    assert cp["driver"] == "ray"
    assert cp["status"] == "pending"
    assert cp["generation"] == 1
    assert cp["enabled"] is True

    r = await client.get(f"{API}/control-planes/{cp['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == cp["id"]
    assert body["config"] == {}
    assert body["observed_generation"] == 0


async def test_control_plane_create_duplicate_name_409(client: AsyncClient, authed_fapp: FastAPI) -> None:
    await make_control_plane(client, name="cp-dup")
    r = await client.post(f"{API}/control-planes", json={"name": "cp-dup", "driver": "ray"})
    assert r.status_code == 409


async def test_control_plane_list(client: AsyncClient, authed_fapp: FastAPI) -> None:
    await make_control_plane(client, name="cp-1")
    await make_control_plane(client, name="cp-2")
    r = await client.get(f"{API}/control-planes")
    assert r.status_code == 200
    names = {item["name"] for item in r.json()}
    assert {"cp-1", "cp-2"} <= names


async def test_control_plane_update_description(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp")
    r = await client.patch(f"{API}/control-planes/{cp['id']}", json={"description": "d1"})
    assert r.status_code == 200
    assert r.json()["description"] == "d1"


async def test_control_plane_update_renames(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp")
    r = await client.patch(f"{API}/control-planes/{cp['id']}", json={"name": "cp-renamed"})
    assert r.status_code == 200
    assert r.json()["name"] == "cp-renamed"


async def test_control_plane_reconcile_queues_environments(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")
    r = await client.post(f"{API}/control-planes/{cp['id']}/reconcile")
    assert r.status_code == 200
    # The route no longer stamps a Phase 1 "no live actions" observation.
    assert "no driver registered" not in str(r.json().get("observed_status"))
    env_body = (await client.get(f"{API}/environments/{env['id']}")).json()
    assert env_body["observed_generation"] < env_body["generation"]  # queued


async def test_control_plane_get_missing_404(client: AsyncClient, authed_fapp: FastAPI) -> None:
    r = await client.get(f"{API}/control-planes/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_control_plane_delete_when_env_exists_409(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp-leaf-guard")
    await make_environment(client, cp["id"], name="env-leaf")
    r = await client.delete(f"{API}/control-planes/{cp['id']}")
    assert r.status_code == 409


async def test_control_plane_delete_when_empty_204(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp")
    r = await client.delete(f"{API}/control-planes/{cp['id']}")
    assert r.status_code == 204
    r2 = await client.get(f"{API}/control-planes/{cp['id']}")
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------


async def test_environment_create_and_get(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env-a")
    assert env["control_plane_id"] == cp["id"]
    assert env["desired_state"] == "running"
    assert env["status"] == "pending"

    r = await client.get(f"{API}/environments/{env['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == env["id"]
    assert r.json()["capabilities"] == {}


async def test_environment_create_missing_cp_404(client: AsyncClient, authed_fapp: FastAPI) -> None:
    r = await client.post(
        f"{API}/environments",
        json={"control_plane_id": str(uuid.uuid4()), "name": "env-x"},
    )
    assert r.status_code == 404


async def test_environment_create_duplicate_name_409(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp")
    await make_environment(client, cp["id"], name="env")
    r = await client.post(
        f"{API}/environments",
        json={"control_plane_id": str(cp["id"]), "name": "env"},
    )
    assert r.status_code == 409


async def test_environment_list_filtered_by_cp(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp1 = await make_control_plane(client, name="cp-1")
    cp2 = await make_control_plane(client, name="cp-2")
    e1 = await make_environment(client, cp1["id"], name="e1")
    await make_environment(client, cp2["id"], name="e2")
    r = await client.get(f"{API}/environments", params={"control_plane_id": str(cp1["id"])})
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()}
    assert ids == {e1["id"]}


async def test_environment_update_desired_state_stops(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")
    r = await client.patch(f"{API}/environments/{env['id']}", json={"desired_state": "stopped"})
    assert r.status_code == 200
    assert r.json()["desired_state"] == "stopped"


async def test_environment_update_invalid_desired_state_409(
    client: AsyncClient, authed_fapp: FastAPI
) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")
    r = await client.patch(f"{API}/environments/{env['id']}", json={"desired_state": "bogus"})
    assert r.status_code == 409


async def test_environment_add_node(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")
    node_id = await make_node(dbsession)
    r = await client.post(
        f"{API}/environments/{env['id']}/nodes",
        json={"node_id": str(node_id), "role": "worker"},
    )
    assert r.status_code == 201
    assert r.json()["id"] == env["id"]


async def test_environment_add_node_invalid_role_409(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")
    node_id = await make_node(dbsession)
    r = await client.post(
        f"{API}/environments/{env['id']}/nodes",
        json={"node_id": str(node_id), "role": "boss"},
    )
    assert r.status_code == 409


async def test_environment_reconcile_queues_for_reconciler(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")
    r = await client.post(f"{API}/environments/{env['id']}/reconcile")
    assert r.status_code == 200
    body = r.json()
    assert body["observed_generation"] < body["generation"]  # queued for the reconciler
    assert "no live actions" not in str(body.get("observed_status"))


async def test_environment_delete_when_deployment_exists_409(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")
    model_id = await make_model(dbsession)
    r = await client.post(
        f"{API}/deployments",
        json={
            "environment_id": str(env["id"]),
            "model_id": str(model_id),
            "name": "dep",
            "spec": good_spec(),
        },
    )
    assert r.status_code == 201, r.text
    r = await client.delete(f"{API}/environments/{env['id']}")
    assert r.status_code == 409


async def test_environment_delete_when_empty_204(client: AsyncClient, authed_fapp: FastAPI) -> None:
    cp = await make_control_plane(client, name="cp")
    env = await make_environment(client, cp["id"], name="env")
    r = await client.delete(f"{API}/environments/{env['id']}")
    assert r.status_code == 204
    r2 = await client.get(f"{API}/environments/{env['id']}")
    assert r2.status_code == 404


async def test_environment_get_missing_404(client: AsyncClient, authed_fapp: FastAPI) -> None:
    r = await client.get(f"{API}/environments/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


async def test_deployment_create_and_get(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env, model_id = await _seed_env_and_model(client, dbsession, "cp-d", "env-d")
    dep = await _create_deploy(client, env, model_id, "dep-a", good_spec(scale={"replicas": 2}))
    assert dep["phase"] == "pending"
    assert dep["desired_state"] == "active"
    assert dep["spec"]["scale"]["replicas"] == 2

    r2 = await client.get(f"{API}/deployments/{dep['id']}")
    assert r2.status_code == 200
    assert r2.json()["id"] == dep["id"]


async def test_deployment_create_missing_env_404(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    model_id = await make_model(dbsession)
    r = await client.post(
        f"{API}/deployments",
        json={
            "environment_id": str(uuid.uuid4()),
            "model_id": str(model_id),
            "name": "dep",
            "spec": good_spec(),
        },
    )
    assert r.status_code == 404


async def test_deployment_create_missing_model_404(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp-m404")
    env = await make_environment(client, cp["id"], name="env-m404")
    r = await client.post(
        f"{API}/deployments",
        json={
            "environment_id": str(env["id"]),
            "model_id": str(uuid.uuid4()),
            "name": "dep-m404",
            "spec": good_spec(),
        },
    )
    assert r.status_code == 404


async def test_deployment_create_missing_api_version_409(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env, model_id = await _seed_env_and_model(client, dbsession, "cp-bad", "env-bad")
    r = await client.post(
        f"{API}/deployments",
        json={
            "environment_id": str(env["id"]),
            "model_id": str(model_id),
            "name": "dep",
            "spec": {"scale": {"replicas": 1}},  # no api_version
        },
    )
    assert r.status_code == 409


async def test_deployment_create_invalid_scale_409(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env, model_id = await _seed_env_and_model(client, dbsession, "cp-uf", "env-uf")
    r = await client.post(
        f"{API}/deployments",
        json={
            "environment_id": str(env["id"]),
            "model_id": str(model_id),
            "name": "dep",
            "spec": good_spec(scale={"replicas": 0}),  # replicas must be >= 1
        },
    )
    assert r.status_code == 409


async def test_deployment_create_duplicate_name_409(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env, model_id = await _seed_env_and_model(client, dbsession, "cp-d2", "env-d2")
    body = {
        "environment_id": str(env["id"]),
        "model_id": str(model_id),
        "name": "dup",
        "spec": good_spec(),
    }
    r1 = await client.post(f"{API}/deployments", json=body)
    assert r1.status_code == 201
    r2 = await client.post(f"{API}/deployments", json=body)
    assert r2.status_code == 409


async def test_deployment_list_filter_by_env(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env1, model_id = await _seed_env_and_model(client, dbsession, "cp-l1", "env-l1")
    env2, _ = await _seed_env_and_model(client, dbsession, "cp-l2", "env-l2")
    await _create_deploy(client, env1, model_id, "d1")
    await _create_deploy(client, env2, model_id, "d2")
    r = await client.get(f"{API}/deployments", params={"environment_id": str(env1["id"])})
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()}
    assert len(ids) == 1


async def test_deployment_update_spec_bumps_generation(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env, model_id = await _seed_env_and_model(client, dbsession, "cp-u1", "env-u1")
    dep = await _create_deploy(client, env, model_id, "u", good_spec(scale={"replicas": 2}))
    gen_before = dep["generation"]
    r = await client.patch(
        f"{API}/deployments/{dep['id']}",
        json={"spec": good_spec(scale={"replicas": 5})},
    )
    assert r.status_code == 200
    assert r.json()["generation"] > gen_before
    assert r.json()["spec"]["scale"]["replicas"] == 5


async def test_deployment_update_desired_state_stops(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env, model_id = await _seed_env_and_model(client, dbsession, "cp-u2", "env-u2")
    dep = await _create_deploy(client, env, model_id, "u2")
    r = await client.patch(f"{API}/deployments/{dep['id']}", json={"desired_state": "stopped"})
    assert r.status_code == 200
    assert r.json()["desired_state"] == "stopped"


async def test_deployment_update_invalid_desired_state_409(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env, model_id = await _seed_env_and_model(client, dbsession, "cp-u3", "env-u3")
    dep = await _create_deploy(client, env, model_id, "u3")
    r = await client.patch(f"{API}/deployments/{dep['id']}", json={"desired_state": "bogus"})
    assert r.status_code == 409


async def test_deployment_reconcile_queues_for_reconciler(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env, model_id = await _seed_env_and_model(client, dbsession, "cp-r1", "env-r1")
    dep = await _create_deploy(client, env, model_id, "r")
    r = await client.post(f"{API}/deployments/{dep['id']}/reconcile")
    assert r.status_code == 200
    body = r.json()
    assert body["observed_generation"] < body["generation"]  # queued for the reconciler
    assert "no live actions" not in str(body.get("observed_status"))


async def test_deployment_delete_204(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    env, model_id = await _seed_env_and_model(client, dbsession, "cp-del", "env-del")
    dep = await _create_deploy(client, env, model_id, "del")
    r = await client.delete(f"{API}/deployments/{dep['id']}")
    assert r.status_code == 204
    r2 = await client.get(f"{API}/deployments/{dep['id']}")
    assert r2.status_code == 404


async def test_deployment_get_missing_404(client: AsyncClient, authed_fapp: FastAPI) -> None:
    r = await client.get(f"{API}/deployments/{uuid.uuid4()}")
    assert r.status_code == 404


async def make_dgx_node(
    dbsession: AsyncSession,
    agent_id: str,
    mgmt_ip: str,
    roce_ip: str,
) -> InfraNode:
    node = InfraNode(
        agent_id=agent_id,
        host=mgmt_ip,
        status="healthy",
        capabilities_json={
            "network": {
                "management_ip": mgmt_ip,
                "fabrics": [
                    {
                        "interface": "enp1s0f1np1",
                        "ip": roce_ip,
                        "cidr": "10.100.0.0/24",
                        "speed_gbps": 200.0,
                        "link_type": "roce",
                        "rdma_device": "rocep1s0f1",
                        "mtu": 9000,
                        "is_up": True,
                        "has_default_route": False,
                    },
                    {
                        "interface": "enP7s7",
                        "ip": mgmt_ip,
                        "cidr": "10.88.10.0/24",
                        "speed_gbps": 1.0,
                        "link_type": "ethernet",
                        "mtu": 1500,
                        "is_up": True,
                        "has_default_route": True,
                    },
                ],
            }
        },
    )
    dbsession.add(node)
    await dbsession.flush()
    return node


async def test_environment_plan_endpoint(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp-plan")
    env = await make_environment(client, cp["id"], name="env-plan")
    n1 = await make_dgx_node(dbsession, "spark-ts3202", "10.88.10.49", "10.100.0.1")
    n2 = await make_dgx_node(dbsession, "spark-3201", "10.88.10.71", "10.100.0.2")

    await client.post(f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n1.id), "role": "head"})
    await client.post(f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n2.id), "role": "worker"})

    r = await client.post(f"{API}/environments/{env['id']}/plan")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["environment_id"] == env["id"]
    assert len(plan["candidates"]) == 2
    rec = next(c for c in plan["candidates"] if c["candidate_id"] == plan["recommended_candidate_id"])
    assert rec["fabric_type"] == "roce"
    assert rec["speed_gbps"] == 200.0
    assert rec["cidr"] == "10.100.0.0/24"
    assert rec["recommended"] is True
    assert plan["recommended_head_node_id"] in {str(n1.id), str(n2.id)}


async def test_environment_apply_plan_endpoint(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp-apply")
    env = await make_environment(client, cp["id"], name="env-apply")
    n1 = await make_dgx_node(dbsession, "spark-ts3202", "10.88.10.49", "10.100.0.1")
    n2 = await make_dgx_node(dbsession, "spark-3201", "10.88.10.71", "10.100.0.2")

    await client.post(f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n1.id), "role": "head"})
    await client.post(f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n2.id), "role": "worker"})

    plan_r = await client.post(f"{API}/environments/{env['id']}/plan")
    assert plan_r.status_code == 200
    plan = plan_r.json()

    apply_r = await client.post(
        f"{API}/environments/{env['id']}/apply-plan",
        json={"plan": plan},
    )
    assert apply_r.status_code == 200, apply_r.text
    applied = apply_r.json()
    resolved = applied["observed_status"]["resolved_fabric"]
    assert resolved["fabric_type"] == "roce"
    assert resolved["speed_gbps"] == 200.0
    assert resolved["node_bindings"][str(n1.id)]["ip"] == "10.100.0.1"
    assert resolved["node_bindings"][str(n2.id)]["ip"] == "10.100.0.2"


async def test_environment_apply_plan_stale_detection_409(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    cp = await make_control_plane(client, name="cp-stale")
    env = await make_environment(client, cp["id"], name="env-stale")
    n1 = await make_dgx_node(dbsession, "spark-ts3202", "10.88.10.49", "10.100.0.1")
    n2 = await make_dgx_node(dbsession, "spark-3201", "10.88.10.71", "10.100.0.2")

    await client.post(f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n1.id), "role": "head"})
    await client.post(f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n2.id), "role": "worker"})

    plan_r = await client.post(f"{API}/environments/{env['id']}/plan")
    assert plan_r.status_code == 200
    plan = plan_r.json()

    # A heartbeat touching the row is not a stale plan: revisions are content
    # digests, not timestamps, so approval survives normal agent traffic.
    n1.updated_at = datetime.now(tz=UTC) + timedelta(seconds=10)
    await dbsession.flush()
    ok_r = await client.post(
        f"{API}/environments/{env['id']}/apply-plan",
        json={"plan": plan},
    )
    assert ok_r.status_code == 200

    # Re-cabled RoCE link: the approved topology no longer exists.
    caps = dict(n1.capabilities_json)
    network = dict(caps["network"])
    fabrics = [dict(f) for f in network["fabrics"]]
    fabrics[0]["ip"] = "10.100.0.9"
    network["fabrics"] = fabrics
    caps["network"] = network
    n1.capabilities_json = caps
    await dbsession.flush()

    apply_r = await client.post(
        f"{API}/environments/{env['id']}/apply-plan",
        json={"plan": plan},
    )
    assert apply_r.status_code == 409
    assert "Stale plan" in apply_r.json()["detail"]


async def test_environment_apply_plan_rejects_nodes_added_after_planning(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    """A node added after the plan was approved has no entry to validate.

    The old per-node loop iterated the revisions in the *request body*, so a
    node joining the environment after planning was invisible to it and the
    cluster would be bound without that member's fabric ever being considered.
    """
    cp = await make_control_plane(client, name="cp-added")
    env = await make_environment(client, cp["id"], name="env-added")
    n1 = await make_dgx_node(dbsession, "spark-add-1", "10.88.10.49", "10.100.0.1")
    n2 = await make_dgx_node(dbsession, "spark-add-2", "10.88.10.71", "10.100.0.2")
    n3 = await make_dgx_node(dbsession, "spark-add-3", "10.88.10.72", "10.100.0.3")

    await client.post(
        f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n1.id), "role": "head"}
    )
    await client.post(
        f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n2.id), "role": "worker"}
    )

    plan = (await client.post(f"{API}/environments/{env['id']}/plan")).json()

    await client.post(
        f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n3.id), "role": "worker"}
    )

    apply_r = await client.post(
        f"{API}/environments/{env['id']}/apply-plan", json={"plan": plan}
    )
    assert apply_r.status_code == 409
    assert "joined the environment after plan generation" in apply_r.json()["detail"]


async def test_environment_apply_plan_ignores_tampered_bindings(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    """Bindings are always re-derived; the body cannot steer the cluster.

    ``apply-plan`` is reachable by anyone holding
    ``inference.environments:operate``, and the bindings it writes are what the
    driver uses to bind the Ray head and join workers.
    """
    cp = await make_control_plane(client, name="cp-tamper")
    env = await make_environment(client, cp["id"], name="env-tamper")
    n1 = await make_dgx_node(dbsession, "spark-tamper-1", "10.88.10.49", "10.100.0.1")
    n2 = await make_dgx_node(dbsession, "spark-tamper-2", "10.88.10.71", "10.100.0.2")

    await client.post(
        f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n1.id), "role": "head"}
    )
    await client.post(
        f"{API}/environments/{env['id']}/nodes", json={"node_id": str(n2.id), "role": "worker"}
    )

    plan = (await client.post(f"{API}/environments/{env['id']}/plan")).json()
    for candidate in plan["candidates"]:
        for binding in candidate["node_bindings"].values():
            binding["ip"] = "203.0.113.7"
            binding["interface"] = "attacker0"

    apply_r = await client.post(
        f"{API}/environments/{env['id']}/apply-plan", json={"plan": plan}
    )
    assert apply_r.status_code == 200
    bound = apply_r.json()["observed_status"]["resolved_fabric"]["node_bindings"]
    assert {b["ip"] for b in bound.values()} == {"10.100.0.1", "10.100.0.2"}
    assert all(b["interface"] == "enp1s0f1np1" for b in bound.values())


@pytest.mark.anyio()
async def test_editing_a_cluster_setting_does_not_delete_its_applied_fabric(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    """Changing a port must not unbind the interconnect.

    ``config_json`` is operator intent and a PATCH replaces it, which is what
    a PATCH of intent should do. The resolved fabric used to be mirrored into
    that same column, so setting ``head_port`` on a bound cluster silently
    deleted the node bindings -- the next reconcile then had no address to
    start the head on, and nothing said why.
    """
    cp = await make_control_plane(client, name="cp-patch")
    env = await make_environment(client, cp["id"], name="env-patch")
    n1 = await make_dgx_node(dbsession, "spark-ts3202", "10.88.10.49", "10.100.0.1")
    n2 = await make_dgx_node(dbsession, "spark-3201", "10.88.10.71", "10.100.0.2")
    await client.post(
        f"{API}/environments/{env['id']}/nodes",
        json={"node_id": str(n1.id), "role": "head"},
    )
    await client.post(
        f"{API}/environments/{env['id']}/nodes",
        json={"node_id": str(n2.id), "role": "worker"},
    )

    plan = (await client.post(f"{API}/environments/{env['id']}/plan")).json()
    applied = (
        await client.post(
            f"{API}/environments/{env['id']}/apply-plan", json={"plan": plan}
        )
    ).json()
    bound = applied["observed_status"]["resolved_fabric"]["node_bindings"]
    assert set(bound) == {str(n1.id), str(n2.id)}

    # The operator moves the head port, as they would on a machine that
    # already has something on 6379.
    patched = (
        await client.patch(
            f"{API}/environments/{env['id']}", json={"config": {"head_port": 6390}}
        )
    ).json()

    assert patched["config"]["head_port"] == 6390
    still_bound = patched["observed_status"]["resolved_fabric"]["node_bindings"]
    assert set(still_bound) == {str(n1.id), str(n2.id)}
    assert still_bound[str(n1.id)]["ip"] == "10.100.0.1"
