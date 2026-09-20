"""Unit tests for Phase 5 gateway source metadata and uniqueness constraints."""

import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.dao.gateway_dao import GatewayDAO
from llm_port_api.db.models.gateway import (
    LLMModelAlias,
    LLMPoolMembership,
    LLMProviderInstance,
    ProviderHealthStatus,
    ProviderType,
)


@pytest.mark.anyio
async def test_provider_instance_source_metadata_and_uniqueness(db_session: AsyncSession) -> None:
    """Test source_kind and source_id columns and their uniqueness semantics."""
    deployment_id = uuid.uuid4()

    # 1. Create first provider instance for this deployment
    inst1 = LLMProviderInstance(
        id=uuid.uuid4(),
        type=ProviderType.VLLM,
        base_url="http://10.88.10.49:8000/llmport-app1",
        enabled=True,
        health_status=ProviderHealthStatus.HEALTHY,
        source_kind="inference_deployment",
        source_id=deployment_id,
    )
    db_session.add(inst1)
    await db_session.flush()

    assert inst1.source_kind == "inference_deployment"
    assert inst1.source_id == deployment_id

    # 2. Attempting to insert another provider instance with the same (source_kind, source_id)
    # must fail with IntegrityError due to partial unique index
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            inst_duplicate = LLMProviderInstance(
                id=uuid.uuid4(),
                type=ProviderType.VLLM,
                base_url="http://10.88.10.71:8000/llmport-app1-dup",
                enabled=True,
                health_status=ProviderHealthStatus.HEALTHY,
                source_kind="inference_deployment",
                source_id=deployment_id,
            )
            db_session.add(inst_duplicate)
            await db_session.flush()


@pytest.mark.anyio
async def test_legacy_rows_with_null_source_can_coexist(db_session: AsyncSession) -> None:
    """Multiple legacy rows with source_kind=None and source_id=None must not collide."""
    inst1 = LLMProviderInstance(
        id=uuid.uuid4(),
        type=ProviderType.VLLM,
        base_url="http://10.0.0.1:8000",
        enabled=True,
        health_status=ProviderHealthStatus.HEALTHY,
        source_kind=None,
        source_id=None,
    )
    inst2 = LLMProviderInstance(
        id=uuid.uuid4(),
        type=ProviderType.VLLM,
        base_url="http://10.0.0.2:8000",
        enabled=True,
        health_status=ProviderHealthStatus.HEALTHY,
        source_kind=None,
        source_id=None,
    )
    db_session.add(inst1)
    db_session.add(inst2)
    await db_session.flush()

    assert inst1.source_kind is None
    assert inst2.source_kind is None


@pytest.mark.anyio
async def test_gateway_dao_resolves_source_metadata(db_session: AsyncSession) -> None:
    """GatewayDAO.resolve_candidates correctly populates source_kind and source_id."""
    alias_name = f"test-alias-{uuid.uuid4().hex[:8]}"
    deployment_id = uuid.uuid4()
    inst_id = uuid.uuid4()

    alias = LLMModelAlias(alias=alias_name, enabled=True)
    inst = LLMProviderInstance(
        id=inst_id,
        type=ProviderType.VLLM,
        base_url="http://10.88.10.49:8000/app",
        enabled=True,
        health_status=ProviderHealthStatus.HEALTHY,
        source_kind="inference_deployment",
        source_id=deployment_id,
    )
    membership = LLMPoolMembership(
        model_alias=alias_name,
        provider_instance_id=inst_id,
        enabled=True,
    )

    db_session.add(alias)
    db_session.add(inst)
    db_session.add(membership)
    await db_session.flush()

    dao = GatewayDAO(db_session)
    candidates = await dao.resolve_candidates(alias=alias_name, tenant_id="default")
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.instance_id == inst_id
    assert cand.source_kind == "inference_deployment"
    assert cand.source_id == deployment_id
