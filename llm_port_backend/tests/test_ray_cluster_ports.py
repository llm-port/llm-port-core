"""A new Ray cluster keeps clear of the ports LLM.Port itself holds.

Found in an end-to-end run on a Windows workstation that is both the server
and the GPU node: Ray's head failed on "port 6379 ... Address already in use"
-- LLM.Port's Redis -- and was retried on the same port every minute.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import InferenceControlPlane
from llm_port_backend.services.inference.drivers.ray.schemas import NEW_CLUSTER_PORTS, RayEnvironmentConfig
from llm_port_backend.services.inference.service import EnvironmentService


async def _control_plane(dbsession: AsyncSession, driver: str) -> InferenceControlPlane:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:12]}", driver=driver)
    dbsession.add(cp)
    await dbsession.flush()
    return cp


async def test_a_new_ray_cluster_is_created_off_redis_and_the_backend(dbsession: AsyncSession) -> None:
    cp = await _control_plane(dbsession, "ray")
    env = await EnvironmentService(dbsession).create(control_plane_id=cp.id, name="workstation")
    config = RayEnvironmentConfig.model_validate(env.config_json)
    assert config.head_port == NEW_CLUSTER_PORTS["head_port"] != 6379
    assert config.serve_http_port == NEW_CLUSTER_PORTS["serve_http_port"] != 8000


async def test_ports_the_caller_chose_are_kept(dbsession: AsyncSession) -> None:
    cp = await _control_plane(dbsession, "ray")
    env = await EnvironmentService(dbsession).create(
        control_plane_id=cp.id, name="dgx", config={"head_port": 6379, "node_env_vars": {"A": "1"}},
    )
    assert env.config_json["head_port"] == 6379
    assert env.config_json["serve_http_port"] == NEW_CLUSTER_PORTS["serve_http_port"]
    assert env.config_json["node_env_vars"] == {"A": "1"}


def test_clusters_made_before_keep_rays_ports() -> None:
    """Their config has no ports; the model's defaults are what they run on."""
    config = RayEnvironmentConfig.model_validate({})
    assert (config.head_port, config.serve_http_port) == (6379, 8000)
