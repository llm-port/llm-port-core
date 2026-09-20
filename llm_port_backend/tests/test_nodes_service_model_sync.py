"""Tests for SYNC_MODEL command result and progress ingestion into ModelAvailability (WI-6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.inference_dao import ModelAvailabilityDAO
from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.inference import (
    ModelAvailability,
    ModelAvailabilityStatus,
)
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommand,
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.services.nodes.service import NodeControlService


@pytest.mark.anyio
async def test_sync_model_progress_and_success_ingestion(dbsession: AsyncSession) -> None:
    # 1. Setup test model and node
    model_id = uuid.uuid4()
    model = LLMModel(
        id=model_id,
        display_name="test-llm-model",
        source=ModelSource.HUGGINGFACE,
        hf_repo_id="org/test-llm-model",
        status=ModelStatus.AVAILABLE,
    )
    dbsession.add(model)

    node_id = uuid.uuid4()
    node = InfraNode(
        id=node_id,
        agent_id="test-agent-01",
        host="10.0.0.1",
        status="healthy",
    )
    dbsession.add(node)
    await dbsession.commit()

    # Initial state: mark as PENDING
    dao = ModelAvailabilityDAO(dbsession)
    row = await dao.mark(
        model_id=model_id,
        node_id=node_id,
        status=ModelAvailabilityStatus.PENDING,
    )
    await dbsession.commit()
    assert row.status == ModelAvailabilityStatus.PENDING.value

    # 2. Setup NodeControlService and command
    node_dao = NodeControlDAO(dbsession)
    service = NodeControlService(
        node_dao,
        pepper="test-pepper",
        enrollment_ttl_minutes=10,
        default_command_timeout_sec=300,
    )

    cmd = await node_dao.create_command(
        node_id=node_id,
        command_type=NodeCommandType.SYNC_MODEL.value,
        payload_json={
            "model_id": str(model_id),
            "manifest_sha256": "digest-alpha-123",
            "model_sync": {
                "model_id": str(model_id),
                "hf_repo_id": "org/test-llm-model",
                "manifest_sha256": "digest-alpha-123",
            },
        },
        idempotency_key=str(uuid.uuid4()),
        issued_by=None,
        correlation_id=str(uuid.uuid4()),
        timeout_sec=300,
    )
    await dbsession.commit()

    # 3. Simulate progress frame: transitions PENDING -> SYNCING
    progress_payload = {
        "progress_pct": 55,
        "message": "Downloading weights shard 2...",
    }
    await service.record_command_progress(
        node_id=node_id,
        command_id=cmd.id,
        payload=progress_payload,
    )
    await dbsession.commit()

    updated = await dao.get(model_id, node_id)
    assert updated is not None
    assert updated.status == ModelAvailabilityStatus.SYNCING.value
    assert updated.progress == 55.0
    assert updated.status_message == "Downloading weights shard 2..."

    # 4. Simulate terminal success: transitions SYNCING -> READY
    success_payload = {
        "success": True,
        "result": {
            "synced": True,
            "model_id": str(model_id),
            "hf_repo_id": "org/test-llm-model",
            "root_path": "/srv/llm-port/models/models--org--test-llm-model/snapshots/commit-abc",
            "revision": "commit-abc",
            "manifest_sha256": "digest-alpha-123",
            "total_size": 42000000,
            "files_synced": 5,
        },
    }
    await service.record_command_result(
        node_id=node_id,
        command_id=cmd.id,
        payload=success_payload,
    )
    await dbsession.commit()

    ready_row = await dao.get(model_id, node_id)
    assert ready_row is not None
    assert ready_row.status == ModelAvailabilityStatus.READY.value
    assert ready_row.progress == 100.0
    assert ready_row.root_path == "/srv/llm-port/models/models--org--test-llm-model/snapshots/commit-abc"
    assert ready_row.revision == "commit-abc"
    assert ready_row.manifest_sha256 == "digest-alpha-123"
    assert ready_row.size_bytes == 42000000
    assert ready_row.ready_at is not None
    assert ready_row.status_message is None


@pytest.mark.anyio
async def test_sync_model_failure_ingestion(dbsession: AsyncSession) -> None:
    model_id = uuid.uuid4()
    model = LLMModel(
        id=model_id,
        display_name="test-llm-fail",
        source=ModelSource.HUGGINGFACE,
        hf_repo_id="org/test-llm-fail",
        status=ModelStatus.AVAILABLE,
    )
    dbsession.add(model)

    node_id = uuid.uuid4()
    node = InfraNode(
        id=node_id,
        agent_id="test-agent-02",
        host="10.0.0.2",
        status="healthy",
    )
    dbsession.add(node)
    await dbsession.commit()

    node_dao = NodeControlDAO(dbsession)
    service = NodeControlService(
        node_dao,
        pepper="test-pepper",
        enrollment_ttl_minutes=10,
        default_command_timeout_sec=300,
    )

    cmd = await node_dao.create_command(
        node_id=node_id,
        command_type=NodeCommandType.SYNC_MODEL.value,
        payload_json={"model_id": str(model_id)},
        idempotency_key=str(uuid.uuid4()),
        issued_by=None,
        correlation_id=str(uuid.uuid4()),
        timeout_sec=300,
    )
    await dbsession.commit()

    fail_payload = {
        "success": False,
        "error_code": "disk_full",
        "error_message": "No space left on device /srv/llm-port/models",
    }
    await service.record_command_result(
        node_id=node_id,
        command_id=cmd.id,
        payload=fail_payload,
    )
    await dbsession.commit()

    dao = ModelAvailabilityDAO(dbsession)
    failed_row = await dao.get(model_id, node_id)
    assert failed_row is not None
    assert failed_row.status == ModelAvailabilityStatus.FAILED.value
    assert "No space left on device" in (failed_row.status_message or "")
