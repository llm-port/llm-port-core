"""Tests for ModelAvailabilityStatus enum, Alembic migration, and ModelAvailabilityDAO (WI-1)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_port_backend.db.dao.inference_dao import ModelAvailabilityDAO
import importlib

migration_mod = importlib.import_module(
    "llm_port_backend.db.migrations.versions.2026-09-20-13-00_m0d3lav41l"
)
from llm_port_backend.db.models.inference import (
    ModelAvailability,
    ModelAvailabilityStatus,
)


def test_model_availability_status_enum_values() -> None:
    """Ensure all required states from Phase 4C are declared."""
    expected = {
        "unknown": ModelAvailabilityStatus.UNKNOWN,
        "pending": ModelAvailabilityStatus.PENDING,
        "syncing": ModelAvailabilityStatus.SYNCING,
        "ready": ModelAvailabilityStatus.READY,
        "stale": ModelAvailabilityStatus.STALE,
        "missing": ModelAvailabilityStatus.MISSING,
        "failed": ModelAvailabilityStatus.FAILED,
    }
    for value, member in expected.items():
        assert member.value == value
        assert ModelAvailabilityStatus(value) == member


def test_migration_metadata_and_upgrade() -> None:
    """Verify migration revision metadata and SQL statements."""
    assert migration_mod.revision == "m0d3lav41l"
    assert migration_mod.down_revision == "i5nf1n6e0d0m1"

    # Verify upgrade executes the required ALTER TYPE commands in autocommit
    op_mock = MagicMock()
    ctx_mock = MagicMock()
    op_mock.get_context.return_value = ctx_mock

    # Monkeypatch alembic op in the module
    orig_op = migration_mod.op
    try:
        migration_mod.op = op_mock
        migration_mod.upgrade()
        calls = [str(c[0][0]) for c in op_mock.execute.call_args_list]
        assert any("ADD VALUE IF NOT EXISTS 'pending'" in c for c in calls)
        assert any("ADD VALUE IF NOT EXISTS 'stale'" in c for c in calls)
        migration_mod.downgrade()  # Must not raise
    finally:
        migration_mod.op = orig_op


@pytest.mark.anyio
async def test_dao_list_for_nodes_empty() -> None:
    """Empty node list short-circuits without executing a DB query."""
    session = AsyncMock()
    dao = ModelAvailabilityDAO(session)
    result = await dao.list_for_nodes(uuid.uuid4(), [])
    assert result == []
    session.execute.assert_not_called()


@pytest.mark.anyio
async def test_dao_list_for_nodes_queries() -> None:
    """Queries for model_id and node_ids in list."""
    session = AsyncMock()
    fake_result = MagicMock()
    m1 = ModelAvailability(id=uuid.uuid4(), model_id=uuid.uuid4(), node_id=uuid.uuid4())
    fake_result.scalars.return_value.all.return_value = [m1]
    session.execute.return_value = fake_result

    dao = ModelAvailabilityDAO(session)
    nodes = [uuid.uuid4(), uuid.uuid4()]
    res = await dao.list_for_nodes(m1.model_id, nodes)
    assert res == [m1]
    session.execute.assert_called_once()


@pytest.mark.anyio
async def test_dao_mark_updates_status_and_timestamps() -> None:
    """mark() stamps updated_at and sets ready_at on READY transition."""
    session = AsyncMock()
    fake_result = MagicMock()
    model_id = uuid.uuid4()
    node_id = uuid.uuid4()
    existing = ModelAvailability(
        id=uuid.uuid4(),
        model_id=model_id,
        node_id=node_id,
        status=ModelAvailabilityStatus.SYNCING.value,
    )
    fake_result.scalar_one_or_none.return_value = existing
    session.execute.return_value = fake_result

    dao = ModelAvailabilityDAO(session)
    marked = await dao.mark(
        model_id=model_id,
        node_id=node_id,
        status=ModelAvailabilityStatus.READY,
        root_path="/models/snapshots/commit1",
    )

    assert marked.status == ModelAvailabilityStatus.READY.value
    assert marked.root_path == "/models/snapshots/commit1"
    assert marked.ready_at is not None
    assert marked.updated_at is not None
