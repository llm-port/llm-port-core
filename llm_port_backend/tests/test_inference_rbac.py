"""RBAC coverage for the /api/inference endpoints.

The conftest tests inject a *superuser* (which bypasses RBAC).  These tests
exercise the real permission matrix by seeding the built-in roles
(``RbacDAO.seed_defaults``) and assigning distinct non-superuser accounts the
expected roles, then asserting allow/deny per action:

* ``viewer``  -> read only (create/update/delete/operate all 403)
* ``operator``-> read + operate (create/update/delete 403)
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
