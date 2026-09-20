"""Tests for Phase 5 generic gateway synchronization in GatewaySyncService."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest

from llm_port_backend.services.llm.gateway_sync import GatewaySyncService, _normalize_base_url


class FakeAsyncSession:
    """Mock async session recording executed statements and parameters."""

    def __init__(self, select_responses: list[Any] | None = None) -> None:
        self.executed: list[tuple[str, dict]] = []
        self.committed = False
        self._select_responses = list(select_responses or [])

    async def execute(self, stmt: Any, params: dict | None = None):
        sql_text = str(stmt.text if hasattr(stmt, "text") else stmt)
        params_dict = dict(params or {})
        self.executed.append((sql_text, params_dict))

        mock_result = MagicMock()
        if "WHERE source_kind = 'inference_deployment' AND source_id = :dep_id" in sql_text:
            val = self._select_responses.pop(0) if self._select_responses else None
            mock_result.scalar.return_value = val
        elif "SELECT id FROM llm_provider_instance" in sql_text:
            ids = self._select_responses.pop(0) if self._select_responses else []
            mock_result.fetchall.return_value = [(i,) for i in ids]
        elif "SELECT count(*)" in sql_text:
            mock_result.scalar.return_value = 0
        return mock_result

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.committed = False

    async def __aenter__(self) -> "FakeAsyncSession":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


@pytest.mark.anyio
async def test_publish_inference_endpoint_first_publish():
    """First publish generates deterministic instance_id and inserts all records."""
    fake_session = FakeAsyncSession(select_responses=[None])  # No existing instance
    session_factory = MagicMock(return_value=fake_session)

    service = GatewaySyncService(session_factory)
    dep_id = uuid.uuid4()
    alias = "meta-llama-3"

    inst_id = await service.publish_inference_endpoint(
        deployment_id=dep_id,
        base_url="http://10.88.10.49:8000/llmport-dep1/v1/",
        alias=alias,
        served_model_name="meta-llama/Meta-Llama-3-8B-Instruct",
        backend_provider_type="vllm",
        health_status="healthy",
        is_routable=True,
        weight=2.5,
        max_concurrency=40,
    )

    expected_inst_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"inference_deployment:{dep_id}")
    assert inst_id == expected_inst_id
    assert fake_session.committed is True

    # Check provider instance insert
    prov_call = next(c for c in fake_session.executed if "INSERT INTO llm_provider_instance" in c[0])
    params = prov_call[1]
    assert params["id"] == expected_inst_id
    assert params["dep_id"] == dep_id
    # Trailing /v1/ stripped
    assert params["base_url"] == "http://10.88.10.49:8000/llmport-dep1"
    assert params["enabled"] is True
    assert params["weight"] == 2.5
    assert params["max_concurrency"] == 40
    assert params["health"] == "healthy"
    assert params["litellm_model"] == "meta-llama/Meta-Llama-3-8B-Instruct"

    # Check alias & membership insert
    alias_call = next(c for c in fake_session.executed if "INSERT INTO llm_model_alias" in c[0])
    assert alias_call[1]["alias"] == alias

    mem_call = next(c for c in fake_session.executed if "INSERT INTO llm_pool_membership" in c[0])
    assert mem_call[1]["alias"] == alias
    assert mem_call[1]["instance_id"] == expected_inst_id
    assert mem_call[1]["enabled"] is True


@pytest.mark.anyio
async def test_publish_inference_endpoint_existing_instance():
    """Existing instance ID is preserved on re-publish."""
    existing_uuid = uuid.uuid4()
    fake_session = FakeAsyncSession(select_responses=[str(existing_uuid)])
    session_factory = MagicMock(return_value=fake_session)

    service = GatewaySyncService(session_factory)
    dep_id = uuid.uuid4()

    inst_id = await service.publish_inference_endpoint(
        deployment_id=dep_id,
        base_url="http://10.88.10.49:8000/llmport-dep1",
        alias="llama",
    )
    assert inst_id == existing_uuid

    prov_call = next(c for c in fake_session.executed if "INSERT INTO llm_provider_instance" in c[0])
    assert prov_call[1]["id"] == existing_uuid


@pytest.mark.anyio
async def test_deactivate_and_reactivate_source():
    """Soft deactivation sets enabled=FALSE without deleting alias/membership."""
    fake_session = FakeAsyncSession()
    session_factory = MagicMock(return_value=fake_session)

    service = GatewaySyncService(session_factory)
    dep_id = uuid.uuid4()

    # Deactivate
    await service.deactivate_source(source_kind="inference_deployment", source_id=dep_id)
    assert fake_session.committed is True
    deact_call = fake_session.executed[-1]
    assert "UPDATE llm_provider_instance" in deact_call[0]
    assert "SET enabled = FALSE" in deact_call[0]
    assert deact_call[1] == {"source_kind": "inference_deployment", "source_id": dep_id}

    # Reactivate
    await service.reactivate_source(source_kind="inference_deployment", source_id=dep_id, health_status="healthy")
    react_call = fake_session.executed[-1]
    assert "UPDATE llm_provider_instance" in react_call[0]
    assert "SET enabled = TRUE" in react_call[0]
    assert react_call[1]["status"] == "healthy"


@pytest.mark.anyio
async def test_retire_and_purge_source():
    """Retire marks unhealthy/disabled; purge removes DB rows."""
    fake_session = FakeAsyncSession(select_responses=[[uuid.uuid4()]])
    session_factory = MagicMock(return_value=fake_session)

    service = GatewaySyncService(session_factory)
    dep_id = uuid.uuid4()

    # Retire
    await service.retire_source(source_kind="inference_deployment", source_id=dep_id)
    retire_call = fake_session.executed[-1]
    assert "SET enabled = FALSE, health_status = 'unhealthy'" in retire_call[0]

    # Purge
    await service.purge_source(source_kind="inference_deployment", source_id=dep_id)
    deleted_tables = [c[0] for c in fake_session.executed if "DELETE FROM" in c[0]]
    assert any("DELETE FROM llm_pool_membership" in sql for sql in deleted_tables)
    assert any("DELETE FROM llm_provider_instance" in sql for sql in deleted_tables)


@pytest.mark.anyio
async def test_publish_runtime_backward_compatible():
    """Legacy publish_runtime continues to work and sets source_kind='runtime'."""
    fake_session = FakeAsyncSession()
    session_factory = MagicMock(return_value=fake_session)

    service = GatewaySyncService(session_factory)
    runtime_id = uuid.uuid4()
    alias = "native-llama"

    await service.publish_runtime(
        runtime_id=runtime_id,
        alias=alias,
        base_url="http://10.0.0.5:8000/v1",
        backend_provider_type="vllm",
        is_remote=False,
    )

    prov_call = next(c for c in fake_session.executed if "INSERT INTO llm_provider_instance" in c[0])
    params = prov_call[1]
    assert params["source_kind"] == "runtime"
    assert params["source_id"] == runtime_id
    assert params["id"] == runtime_id
    assert params["base_url"] == "http://10.0.0.5:8000"
