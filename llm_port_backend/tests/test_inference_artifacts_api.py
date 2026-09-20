"""API tests for independent artifact sync and readiness routes (WI-7)."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.inference_dao import ControlPlaneDAO, EnvironmentDAO, EnvironmentNodeDAO
from llm_port_backend.db.dao.llm_dao import ModelDAO
from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.models.inference import (
    EnvironmentNodeRole,
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.db.models.node_control import InfraNode
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


@pytest.mark.anyio
async def test_artifact_api_rbac_viewer_and_operator(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """RBAC check: viewer can GET readiness, but cannot POST sync (403); operator can do both."""
    viewer, operator = await _seed_viewer_operator(dbsession)

    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()

    node = InfraNode(
        agent_id=f"node-{uuid.uuid4().hex[:8]}",
        host="10.0.0.1",
        status="healthy",
        scheduler_eligible=True,
    )
    dbsession.add(node)
    await dbsession.flush()

    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:8]}",
        head_node_id=node.id,
    )
    dbsession.add(env)
    await dbsession.flush()
    dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))

    model = LLMModel(
        display_name="org/test-model",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="org/test-model",
        hf_revision="main",
    )
    dbsession.add(model)
    await dbsession.commit()

    # 1. As viewer: GET is allowed (read permission), POST is 403 (needs operate permission)
    _set_user(fastapi_app, viewer)

    r_get = await client.get(f"{API}/environments/{env.id}/artifacts/{model.id}")
    assert r_get.status_code == 200
    data = r_get.json()
    assert data["model_id"] == str(model.id)
    assert data["all_ready"] is False

    r_post = await client.post(f"{API}/environments/{env.id}/artifacts/{model.id}/sync")
    assert r_post.status_code == 403

    # 2. As operator: both GET and POST are allowed
    _set_user(fastapi_app, operator)

    r_get_op = await client.get(f"{API}/environments/{env.id}/artifacts/{model.id}")
    assert r_get_op.status_code == 200

    r_post_op = await client.post(f"{API}/environments/{env.id}/artifacts/{model.id}/sync")
    assert r_post_op.status_code == 200
    post_data = r_post_op.json()
    assert post_data["model_id"] == str(model.id)


@pytest.mark.anyio
async def test_artifact_api_returns_409_when_no_eligible_nodes(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """Both GET and POST return 409 Conflict when environment has no eligible nodes."""
    _, operator = await _seed_viewer_operator(dbsession)
    _set_user(fastapi_app, operator)

    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()

    # Environment with no members
    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-empty-{uuid.uuid4().hex[:8]}",
    )
    dbsession.add(env)

    model = LLMModel(
        display_name="org/test-model",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="org/test-model",
        hf_revision="main",
    )
    dbsession.add(model)
    await dbsession.commit()

    # GET must 409
    r_get = await client.get(f"{API}/environments/{env.id}/artifacts/{model.id}")
    assert r_get.status_code == 409
    assert "eligible nodes" in r_get.json()["detail"].lower()

    # POST must 409
    r_post = await client.post(f"{API}/environments/{env.id}/artifacts/{model.id}/sync")
    assert r_post.status_code == 409
    assert "eligible nodes" in r_post.json()["detail"].lower()


@pytest.mark.anyio
async def test_artifact_api_returns_404_for_missing_env_or_model(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """404 Not Found for non-existent environment or model."""
    _, operator = await _seed_viewer_operator(dbsession)
    _set_user(fastapi_app, operator)

    non_env = uuid.uuid4()
    non_model = uuid.uuid4()

    r1 = await client.get(f"{API}/environments/{non_env}/artifacts/{non_model}")
    assert r1.status_code == 404

    r2 = await client.post(f"{API}/environments/{non_env}/artifacts/{non_model}/sync")
    assert r2.status_code == 404

