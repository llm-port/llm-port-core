"""The models this server keeps, as the marketplace's "On this server" view reads them."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.llm import (
    ArtifactFormat,
    DownloadJob,
    DownloadJobStatus,
    LLMModel,
    ModelArtifact,
    ModelSource,
    ModelStatus,
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
def authed(fastapi_app: FastAPI) -> FastAPI:
    from llm_port_backend.db.models.users import User, current_active_user

    user = MagicMock(spec=User)
    user.id, user.is_active, user.is_superuser, user.is_verified = uuid.uuid4(), True, True, True
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    return fastapi_app


async def test_each_kept_model_says_its_size_its_download_and_what_uses_it(
    authed: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    from llm_port_backend.db.models.inference import InferenceControlPlane, InferenceDeployment, InferenceEnvironment

    ready = LLMModel(display_name="Qwen3-0.6B", source=ModelSource.HUGGINGFACE, status=ModelStatus.AVAILABLE,
                     hf_repo_id="Qwen/Qwen3-0.6B")
    fetching = LLMModel(display_name="Qwen3-8B", source=ModelSource.HUGGINGFACE, status=ModelStatus.DOWNLOADING,
                        hf_repo_id="Qwen/Qwen3-8B")
    mine = LLMModel(display_name="my-finetune", source=ModelSource.LOCAL_PATH, status=ModelStatus.AVAILABLE)
    dbsession.add_all([ready, fetching, mine])
    await dbsession.flush()
    dbsession.add(ModelArtifact(model_id=ready.id, format=ArtifactFormat.SAFETENSORS,
                                path="/models/x/model.safetensors", size_bytes=1_503_300_328))
    dbsession.add(DownloadJob(model_id=fetching.id, status=DownloadJobStatus.RUNNING, progress=42))
    plane = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(plane)
    await dbsession.flush()
    env = InferenceEnvironment(control_plane_id=plane.id, name="dgx-pair")
    dbsession.add(env)
    await dbsession.flush()
    dbsession.add(InferenceDeployment(environment_id=env.id, model_id=ready.id, name="qwen3-0-6b", spec_json={}))
    await dbsession.flush()

    r = await client.get("/api/llm/marketplace/kept")
    assert r.status_code == 200, r.text
    items = {i["display_name"]: i for i in r.json()["items"]}

    assert items["Qwen3-0.6B"]["size_bytes"] == 1_503_300_328
    assert items["Qwen3-0.6B"]["deployments"][0] == {
        "id": items["Qwen3-0.6B"]["deployments"][0]["id"], "name": "qwen3-0-6b", "cluster": "dgx-pair",
        "phase": "pending", "desired_state": "active",
    }
    assert items["Qwen3-8B"]["download"]["status"] == "running"
    assert items["Qwen3-8B"]["download"]["progress"] == 42
    assert items["my-finetune"]["source"] == "local_path"
    assert items["my-finetune"]["hf_repo_id"] is None
