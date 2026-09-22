"""RBAC coverage for the /api/inference endpoints.

The conftest tests inject a *superuser* (which bypasses RBAC).  These tests
exercise the real permission matrix by seeding the built-in roles
(``RbacDAO.seed_defaults``) and assigning distinct non-superuser accounts the
expected roles, then asserting allow/deny per action:

* ``viewer``  -> read only (create/update/delete/operate all 403)
* ``operator``-> read + operate everywhere, plus create/update on environments
  and deployments (the lifecycle it already has on ``llm.runtimes``);
  ``delete`` anywhere and any write to a control plane stay admin-only.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.models.users import User, current_active_user

API = "/api/inference"


async def _seed_viewer_operator(dbsession: AsyncSession) -> tuple[User, User]:
    """Seed built-in roles and return (viewer, operator) accounts."""
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()

    viewer = User(
        email=f"viewer-{uuid.uuid4().hex}@test.local",
        hashed_password="x",
        is_verified=True,
        is_active=True,
        is_superuser=False,
    )
    operator = User(
        email=f"operator-{uuid.uuid4().hex}@test.local",
        hashed_password="x",
        is_verified=True,
        is_active=True,
        is_superuser=False,
    )
    dbsession.add(viewer)
    dbsession.add(operator)
    await dbsession.flush()

    await rbac.assign_role(viewer.id, (await rbac.get_role_by_name("viewer")).id)
    await rbac.assign_role(operator.id, (await rbac.get_role_by_name("operator")).id)
    return viewer, operator


def _set_user(fastapi_app: FastAPI, user: User) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: user


# ---------------------------------------------------------------------------
# Seed defaults expose the inference permissions
# ---------------------------------------------------------------------------


async def test_seed_defaults_includes_inference_permissions(dbsession: AsyncSession) -> None:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    perms = await rbac.list_permissions()
    kinds = {(p.resource, p.action) for p in perms}
    assert ("inference.control_planes", "read") in kinds
    assert ("inference.environments", "operate") in kinds
    assert ("inference.deployments", "create") in kinds
    # role->permission links exist for the builtin roles.
    viewer = await rbac.get_role_by_name("viewer")
    assert viewer is not None
    assert not await rbac.has_permission(
        uuid.uuid4(), "inference.control_planes", "create"
    )


# ---------------------------------------------------------------------------
# Viewer: read-only
# ---------------------------------------------------------------------------


async def test_viewer_can_read_but_not_write(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession
) -> None:
    viewer, _ = await _seed_viewer_operator(dbsession)
    _set_user(fastapi_app, viewer)

    r = await client.get(f"{API}/control-planes")
    assert r.status_code == 200
    r = await client.get(f"{API}/environments")
    assert r.status_code == 200
    r = await client.get(f"{API}/deployments")
    assert r.status_code == 200

    # create / update / delete / operate are all denied for a bare viewer.
    r = await client.post(
        f"{API}/control-planes", json={"name": "viewer-cp", "driver": "ray"}
    )
    assert r.status_code == 403
    r = await client.patch(f"{API}/control-planes/{uuid.uuid4()}", json={"description": "x"})
    assert r.status_code == 403
    r = await client.delete(f"{API}/control-planes/{uuid.uuid4()}")
    assert r.status_code == 403
    r = await client.post(f"{API}/control-planes/{uuid.uuid4()}/reconcile")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Operator: read + operate
# ---------------------------------------------------------------------------


async def test_operator_can_operate_but_not_create(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession
) -> None:
    # Seed a control plane and an environment via the DAO so the operator has
    # something to "operate" on without needing create permission.
    _viewer, operator = await _seed_viewer_operator(dbsession)
    from llm_port_backend.db.dao.inference_dao import (  # noqa: PLC0415
        ControlPlaneDAO,
        EnvironmentDAO,
    )

    cp = await ControlPlaneDAO(dbsession).create(name="op-cp", driver="ray")
    env = await EnvironmentDAO(dbsession).create(
        control_plane_id=cp.id, name="op-env"
    )
    await dbsession.flush()
    _set_user(fastapi_app, operator)

    # operate -> allowed (200)
    r = await client.post(f"{API}/control-planes/{cp.id}/reconcile")
    assert r.status_code == 200
    r = await client.post(f"{API}/environments/{env.id}/reconcile")
    assert r.status_code == 200

    # read -> allowed
    r = await client.get(f"{API}/control-planes")
    assert r.status_code == 200

    # create / update / delete -> denied
    r = await client.post(f"{API}/control-planes", json={"name": "op-cp2", "driver": "ray"})
    assert r.status_code == 403
    r = await client.patch(f"{API}/control-planes/{cp.id}", json={"description": "x"})
    assert r.status_code == 403
    r = await client.delete(f"{API}/control-planes/{cp.id}")
    assert r.status_code == 403


async def test_operator_runs_the_environment_lifecycle(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession
) -> None:
    """G-5: the lifecycle verbs an operator needs to run the Phase 6 pass.

    Creating an environment, adding a member and patching it are the operator's
    equivalent of ``start``/``stop``/``restart`` on ``llm.runtimes``.  Deleting
    one is not: teardown stays with admin.
    """
    _viewer, operator = await _seed_viewer_operator(dbsession)
    from llm_port_backend.db.dao.inference_dao import ControlPlaneDAO  # noqa: PLC0415
    from llm_port_backend.db.models.node_control import InfraNode  # noqa: PLC0415

    cp = await ControlPlaneDAO(dbsession).create(name="op-lifecycle-cp", driver="ray")
    node = InfraNode(agent_id=f"agent-{uuid.uuid4().hex}", host="node-host")
    dbsession.add(node)
    await dbsession.flush()
    _set_user(fastapi_app, operator)

    # create -> allowed
    r = await client.post(
        f"{API}/environments",
        json={"control_plane_id": str(cp.id), "name": "op-lifecycle-env"},
    )
    assert r.status_code == 201, r.text
    env_id = r.json()["id"]

    # update: add a member, then patch the environment -> allowed
    r = await client.post(
        f"{API}/environments/{env_id}/nodes",
        json={"node_id": str(node.id), "role": "worker"},
    )
    assert r.status_code == 201, r.text
    r = await client.patch(f"{API}/environments/{env_id}", json={"description": "scaled"})
    assert r.status_code == 200, r.text

    # delete -> still admin-only
    r = await client.delete(f"{API}/environments/{env_id}")
    assert r.status_code == 403
