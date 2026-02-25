"""DAO for system settings and apply orchestration."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.system_settings import (
    InfraAgent,
    InfraAgentStatus,
    SystemApplyEventResult,
    SystemApplyJob,
    SystemApplyJobEvent,
    SystemApplyStatus,
    SystemSettingSecret,
    SystemSettingValue,
)


class SystemSettingsDAO:
    """Persistence helper for system settings resources."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def list_values(self) -> list[SystemSettingValue]:
        """Return all non-secret setting values."""
        result = await self.session.execute(select(SystemSettingValue))
        return list(result.scalars().all())

    async def get_value(self, key: str) -> SystemSettingValue | None:
        """Get one non-secret value by key."""
        result = await self.session.execute(
            select(SystemSettingValue).where(SystemSettingValue.key == key),
        )
        return result.scalar_one_or_none()

    async def upsert_value(
        self,
        key: str,
        value_json: dict[str, Any],
        updated_by: uuid.UUID | None,
    ) -> SystemSettingValue:
        """Insert or update a non-secret setting."""
        existing = await self.get_value(key)
        if existing is None:
            obj = SystemSettingValue(
                key=key,
                value_json=value_json,
                updated_by=updated_by,
                version=1,
            )
            self.session.add(obj)
            return obj
        existing.value_json = value_json
        existing.updated_by = updated_by
        existing.version += 1
        return existing

    async def list_secrets(self) -> list[SystemSettingSecret]:
        """Return all secret rows."""
        result = await self.session.execute(select(SystemSettingSecret))
        return list(result.scalars().all())

    async def get_secret(self, key: str) -> SystemSettingSecret | None:
        """Get one secret row by key."""
        result = await self.session.execute(
            select(SystemSettingSecret).where(SystemSettingSecret.key == key),
        )
        return result.scalar_one_or_none()

    async def upsert_secret(
        self,
        key: str,
        ciphertext: str,
        nonce: str | None,
        kek_version: str,
        updated_by: uuid.UUID | None,
    ) -> SystemSettingSecret:
        """Insert or update a secret row."""
        existing = await self.get_secret(key)
        if existing is None:
            obj = SystemSettingSecret(
                key=key,
                ciphertext=ciphertext,
                nonce=nonce,
                kek_version=kek_version,
                updated_by=updated_by,
            )
            self.session.add(obj)
            return obj
        existing.ciphertext = ciphertext
        existing.nonce = nonce
        existing.kek_version = kek_version
        existing.updated_by = updated_by
        return existing

    async def create_apply_job(
        self,
        status: SystemApplyStatus,
        target_host: str,
        triggered_by: uuid.UUID | None,
        change_set_json: dict[str, Any],
        previous_snapshot_json: dict[str, Any] | None,
    ) -> SystemApplyJob:
        """Create an apply job."""
        job = SystemApplyJob(
            status=status.value,
            target_host=target_host,
            triggered_by=triggered_by,
            change_set_json=change_set_json,
            previous_snapshot_json=previous_snapshot_json,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def get_apply_job(self, job_id: uuid.UUID) -> SystemApplyJob | None:
        """Fetch an apply job by id."""
        result = await self.session.execute(
            select(SystemApplyJob).where(SystemApplyJob.id == job_id),
        )
        return result.scalar_one_or_none()

    async def list_apply_job_events(self, job_id: uuid.UUID) -> list[SystemApplyJobEvent]:
        """Return events for a job in sequence order."""
        result = await self.session.execute(
            select(SystemApplyJobEvent)
            .where(SystemApplyJobEvent.job_id == job_id)
            .order_by(SystemApplyJobEvent.seq.asc()),
        )
        return list(result.scalars().all())

    async def append_apply_event(
        self,
        job_id: uuid.UUID,
        seq: int,
        service: str,
        action: str,
        result: SystemApplyEventResult,
        message: str,
    ) -> SystemApplyJobEvent:
        """Append one apply event."""
        event = SystemApplyJobEvent(
            job_id=job_id,
            seq=seq,
            service=service,
            action=action,
            result=result.value,
            message=message,
        )
        self.session.add(event)
        return event

    async def set_apply_job_status(
        self,
        job: SystemApplyJob,
        status: SystemApplyStatus,
        error: str | None = None,
        ended: bool = False,
    ) -> SystemApplyJob:
        """Update apply job status and optional end marker."""
        job.status = status.value
        job.error = error
        if ended:
            job.ended_at = datetime.now(tz=UTC)
        return job

    async def upsert_agent(
        self,
        agent_id: str,
        host: str,
        status: InfraAgentStatus,
        capabilities: dict[str, Any],
        version: str | None,
    ) -> InfraAgent:
        """Insert/update agent registration and heartbeat."""
        result = await self.session.execute(
            select(InfraAgent).where(InfraAgent.id == agent_id),
        )
        existing = result.scalar_one_or_none()
        now = datetime.now(tz=UTC)
        if existing is None:
            agent = InfraAgent(
                id=agent_id,
                host=host,
                status=status.value,
                capabilities=capabilities,
                version=version,
                last_seen=now,
            )
            self.session.add(agent)
            return agent
        existing.host = host
        existing.status = status.value
        existing.capabilities = capabilities
        existing.version = version
        existing.last_seen = now
        return existing

    async def list_agents(self) -> list[InfraAgent]:
        """List all known infra agents."""
        result = await self.session.execute(select(InfraAgent))
        return list(result.scalars().all())
