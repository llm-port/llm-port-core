import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.node_control import InfraNode, NodeCommandStatus
from llm_port_backend.services.inference.drivers.ray.commands import NodeCommandGateway


@pytest.fixture
async def sample_node(dbsession: AsyncSession) -> InfraNode:
    node = InfraNode(
        agent_id=f"agent-{uuid.uuid4().hex[:8]}",
        host="127.0.0.1",
        status="healthy",
    )
    dbsession.add(node)
    await dbsession.flush()
    await dbsession.refresh(node)
    return node


async def test_gateway_issue_and_read(dbsession: AsyncSession, sample_node: InfraNode) -> None:
    gateway = NodeCommandGateway(dbsession)
    key = f"test-idem-key-{uuid.uuid4().hex[:8]}"

    cmd = await gateway.issue(
        node_id=sample_node.id,
        command_type="get_ray_status",
        payload={"dummy": "data"},
        idempotency_key=key,
    )
    assert cmd.id is not None
    assert cmd.command_type == "get_ray_status"
    assert cmd.idempotency_key == key

    # Test issued_at wall-clock time
    now = datetime.now(UTC)
    assert abs((now - cmd.issued_at).total_seconds()) < 5

    # Test get_command
    read_cmd = await gateway.get_command(cmd.id)
    assert read_cmd is not None
    assert read_cmd.id == cmd.id


async def test_gateway_idempotent_resume(dbsession: AsyncSession, sample_node: InfraNode) -> None:
    gateway = NodeCommandGateway(dbsession)
    key = f"test-resume-key-{uuid.uuid4().hex[:8]}"

    cmd1 = await gateway.issue(
        node_id=sample_node.id,
        command_type="run_serve_app",
        payload={"app": "test"},
        idempotency_key=key,
    )

    cmd2 = await gateway.issue(
        node_id=sample_node.id,
        command_type="run_serve_app",
        payload={"app": "test"},
        idempotency_key=key,
    )

    assert cmd1.id == cmd2.id


async def test_gateway_wait_terminal(dbsession: AsyncSession, sample_node: InfraNode) -> None:
    gateway = NodeCommandGateway(dbsession)
    key = f"test-wait-key-{uuid.uuid4().hex[:8]}"

    cmd = await gateway.issue(
        node_id=sample_node.id,
        command_type="run_serve_app",
        payload={"app": "test"},
        idempotency_key=key,
    )

    async def mark_success():
        await asyncio.sleep(0.5)
        cmd.status = NodeCommandStatus.SUCCEEDED.value
        await dbsession.flush()

    task = asyncio.create_task(mark_success())
    finished = await gateway.wait(cmd.id, budget_sec=5.0, poll_interval_sec=0.2)
    await task

    assert finished is not None
    assert finished.status == NodeCommandStatus.SUCCEEDED.value


async def test_gateway_wait_timeout(dbsession: AsyncSession, sample_node: InfraNode) -> None:
    gateway = NodeCommandGateway(dbsession)
    key = f"test-timeout-key-{uuid.uuid4().hex[:8]}"

    cmd = await gateway.issue(
        node_id=sample_node.id,
        command_type="run_serve_app",
        payload={"app": "test"},
        idempotency_key=key,
    )

    # Command remains in queued/non-terminal state
    finished = await gateway.wait(cmd.id, budget_sec=0.6, poll_interval_sec=0.2)
    assert finished is None
