"""Deleting a cluster must not leave Ray running on its machines.

The console had no way to delete a cluster at all. The API did -- by deleting
the row, which does not reach the machines, so a running cluster deleted that
way left its Ray head going on the hardware with nothing left to stop it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.service import EnvironmentService, InferenceError
from tests.test_inference_env_observation import _bound_environment


async def _running(dbsession: AsyncSession):
    env = await _bound_environment(dbsession)
    env.desired_state = "running"
    env.status = "ready"
    await dbsession.flush()
    return env


async def _set_members(dbsession: AsyncSession, status: str) -> None:
    for node in (await dbsession.execute(select(InfraNode))).scalars():
        node.status = status
    await dbsession.flush()


@pytest.mark.anyio()
async def test_a_running_cluster_is_not_deleted_out_from_under_its_machines(
    dbsession: AsyncSession,
) -> None:
    env = await _running(dbsession)

    with pytest.raises(InferenceError, match="stop it first"):
        await EnvironmentService(session=dbsession).delete(env.id)


@pytest.mark.anyio()
async def test_force_is_honoured_only_when_no_machine_can_be_reached(
    dbsession: AsyncSession,
) -> None:
    env = await _running(dbsession)
    await _set_members(dbsession, "healthy")

    with pytest.raises(InferenceError, match="stop it first"):
        await EnvironmentService(session=dbsession).delete(env.id, force=True)


@pytest.mark.anyio()
async def test_a_cluster_whose_machines_are_all_gone_can_be_deleted(
    dbsession: AsyncSession,
) -> None:
    """Nothing can stop it from here, so refusing would only strand the row."""
    env = await _running(dbsession)
    await _set_members(dbsession, "offline")

    await EnvironmentService(session=dbsession).delete(env.id, force=True)


@pytest.mark.anyio()
async def test_a_failed_cluster_deletes_without_ceremony(dbsession: AsyncSession) -> None:
    env = await _bound_environment(dbsession)
    env.status = "failed"
    await dbsession.flush()

    await EnvironmentService(session=dbsession).delete(env.id)
