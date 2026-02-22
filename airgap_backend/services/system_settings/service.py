"""System settings service with validation, encryption, and apply orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from airgap_backend.db.dao.system_settings_dao import SystemSettingsDAO
from airgap_backend.db.models.system_settings import (
    InfraAgentStatus,
    SystemApplyEventResult,
    SystemApplyScope,
    SystemApplyStatus,
)
from airgap_backend.services.system_settings.crypto import SettingsCrypto
from airgap_backend.services.system_settings.executors import ApplyAction, ApplyExecutor
from airgap_backend.services.system_settings.registry import (
    SETTINGS_REGISTRY,
    SettingDefinition,
    registry_by_key,
    validate_value,
)


@dataclass(frozen=True)
class ApplySummary:
    """Result envelope returned to API layer."""

    job_id: str | None
    apply_status: str
    apply_scope: str
    messages: list[str]


class SystemSettingsService:
    """System settings orchestration service."""

    def __init__(
        self,
        dao: SystemSettingsDAO,
        crypto: SettingsCrypto,
        local_executor: ApplyExecutor,
        agent_executor: ApplyExecutor,
        agent_enabled: bool,
    ) -> None:
        self._dao = dao
        self._crypto = crypto
        self._local_executor = local_executor
        self._agent_executor = agent_executor
        self._agent_enabled = agent_enabled
        self._registry = registry_by_key()

    def schema(self) -> list[dict[str, Any]]:
        """Return schema metadata for UI rendering."""
        return [
            {
                "key": item.key,
                "type": item.type,
                "category": item.category,
                "group": item.group,
                "label": item.label,
                "description": item.description,
                "is_secret": item.is_secret,
                "default": item.default if not item.is_secret else "",
                "apply_scope": item.apply_scope.value,
                "service_targets": list(item.service_targets),
                "protected": item.protected,
                "enum_values": list(item.enum_values),
            }
            for item in SETTINGS_REGISTRY
        ]

    async def list_values(self) -> dict[str, dict[str, Any]]:
        """Return effective values with masked secret previews."""
        values = {row.key: row for row in await self._dao.list_values()}
        secrets = {row.key: row for row in await self._dao.list_secrets()}
        payload: dict[str, dict[str, Any]] = {}
        for defn in SETTINGS_REGISTRY:
            if defn.is_secret:
                secret = secrets.get(defn.key)
                if secret is None:
                    payload[defn.key] = {"configured": False, "masked": "", "is_secret": True}
                else:
                    plain = self._crypto.decrypt(secret.ciphertext)
                    payload[defn.key] = {
                        "configured": True,
                        "masked": self._crypto.mask(plain),
                        "is_secret": True,
                    }
                continue

            row = values.get(defn.key)
            payload[defn.key] = {
                "value": row.value_json.get("value", defn.default) if row else defn.default,
                "is_secret": False,
            }
        return payload

    async def update_value(
        self,
        *,
        key: str,
        value: object,
        actor_id: uuid.UUID | None,
        root_mode_active: bool,
        target_host: str = "local",
    ) -> ApplySummary:
        """Update one setting and execute immediate apply semantics."""
        defn = self._registry.get(key)
        if defn is None:
            msg = f"Unknown setting key: {key}"
            raise KeyError(msg)
        if defn.protected and not root_mode_active:
            msg = f"Setting '{key}' requires active root mode."
            raise PermissionError(msg)

        validated = validate_value(defn, value)

        if defn.is_secret:
            ciphertext = self._crypto.encrypt(str(validated))
            await self._dao.upsert_secret(
                key=key,
                ciphertext=ciphertext,
                nonce=None,
                kek_version="v1",
                updated_by=actor_id,
            )
        else:
            await self._dao.upsert_value(
                key=key,
                value_json={"value": validated},
                updated_by=actor_id,
            )

        if defn.apply_scope == SystemApplyScope.LIVE_RELOAD:
            return ApplySummary(
                job_id=None,
                apply_status="success",
                apply_scope=defn.apply_scope.value,
                messages=[f"Applied {key} as live reload setting."],
            )

        return await self._run_apply_for_keys(
            [defn],
            actor_id=actor_id,
            target_host=target_host,
        )

    async def _run_apply_for_keys(
        self,
        defs: list[SettingDefinition],
        *,
        actor_id: uuid.UUID | None,
        target_host: str,
    ) -> ApplySummary:
        scope = (
            SystemApplyScope.STACK_RECREATE
            if any(defn.apply_scope == SystemApplyScope.STACK_RECREATE for defn in defs)
            else SystemApplyScope.SERVICE_RESTART
        )
        services = tuple(
            sorted({service for defn in defs for service in defn.service_targets}),
        )
        keys = tuple(defn.key for defn in defs)
        action = ApplyAction(scope=scope, services=services, changed_keys=keys)
        snapshot = await self.list_values()

        job = await self._dao.create_apply_job(
            status=SystemApplyStatus.PENDING,
            target_host=target_host,
            triggered_by=actor_id,
            change_set_json={
                "scope": scope.value,
                "services": list(services),
                "changed_keys": list(keys),
            },
            previous_snapshot_json=snapshot,
        )

        seq = 1
        await self._dao.append_apply_event(
            job.id,
            seq,
            service="system",
            action="apply.start",
            result=SystemApplyEventResult.INFO,
            message=f"Starting apply for scope={scope.value}",
        )
        seq += 1

        await self._dao.set_apply_job_status(job, status=SystemApplyStatus.RUNNING)

        try:
            executor = self._local_executor
            if target_host != "local":
                if not self._agent_enabled:
                    msg = "Remote target requested but agent execution is disabled."
                    raise RuntimeError(msg)
                executor = self._agent_executor
            messages = await executor.execute(action, target_host)
            for message in messages:
                await self._dao.append_apply_event(
                    job.id,
                    seq,
                    service="executor",
                    action="apply.step",
                    result=SystemApplyEventResult.SUCCESS,
                    message=message,
                )
                seq += 1
            await self._dao.set_apply_job_status(job, status=SystemApplyStatus.SUCCESS, ended=True)
            return ApplySummary(
                job_id=str(job.id),
                apply_status="success",
                apply_scope=scope.value,
                messages=messages,
            )
        except Exception as exc:
            await self._dao.append_apply_event(
                job.id,
                seq,
                service="executor",
                action="apply.failed",
                result=SystemApplyEventResult.FAILED,
                message=str(exc),
            )
            seq += 1
            rollback_ok = await self._attempt_rollback(
                job_id=job.id,
                seq_start=seq,
                action=action,
                target_host=target_host,
                reason=str(exc),
            )
            await self._dao.set_apply_job_status(
                job,
                status=SystemApplyStatus.FAILED if rollback_ok else SystemApplyStatus.ROLLBACK_FAILED,
                error=str(exc),
                ended=True,
            )
            return ApplySummary(
                job_id=str(job.id),
                apply_status="failed",
                apply_scope=scope.value,
                messages=[str(exc), "Rollback attempted."],
            )

    async def _attempt_rollback(
        self,
        *,
        job_id: uuid.UUID,
        seq_start: int,
        action: ApplyAction,
        target_host: str,
        reason: str,
    ) -> bool:
        """Run best-effort rollback for failed non-live apply actions."""
        await self._dao.append_apply_event(
            job_id,
            seq_start,
            service="rollback",
            action="rollback.start",
            result=SystemApplyEventResult.INFO,
            message=f"Rollback started after failure: {reason}",
        )
        seq = seq_start + 1
        job = await self._dao.get_apply_job(job_id)
        if job is None:
            return False
        await self._dao.set_apply_job_status(job, status=SystemApplyStatus.ROLLBACK_RUNNING)

        executor = self._local_executor
        if target_host != "local":
            if not self._agent_enabled:
                await self._dao.append_apply_event(
                    job_id,
                    seq,
                    service="rollback",
                    action="rollback.skipped",
                    result=SystemApplyEventResult.FAILED,
                    message="Rollback skipped: agent execution disabled for remote target.",
                )
                return False
            executor = self._agent_executor
        try:
            messages = await executor.execute(action, target_host)
            for message in messages:
                await self._dao.append_apply_event(
                    job_id,
                    seq,
                    service="rollback",
                    action="rollback.step",
                    result=SystemApplyEventResult.SUCCESS,
                    message=message,
                )
                seq += 1
            await self._dao.append_apply_event(
                job_id,
                seq,
                service="rollback",
                action="rollback.done",
                result=SystemApplyEventResult.SUCCESS,
                message="Rollback completed.",
            )
            return True
        except Exception as rollback_exc:
            await self._dao.append_apply_event(
                job_id,
                seq,
                service="rollback",
                action="rollback.failed",
                result=SystemApplyEventResult.FAILED,
                message=str(rollback_exc),
            )
            return False

    async def get_apply_job(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        """Return one apply job with ordered events."""
        job = await self._dao.get_apply_job(job_id)
        if job is None:
            return None
        events = await self._dao.list_apply_job_events(job_id)
        return {
            "id": str(job.id),
            "status": job.status,
            "target_host": job.target_host,
            "triggered_by": str(job.triggered_by) if job.triggered_by else None,
            "change_set": job.change_set_json,
            "error": job.error,
            "started_at": job.started_at.isoformat(),
            "ended_at": job.ended_at.isoformat() if job.ended_at else None,
            "events": [
                {
                    "seq": event.seq,
                    "service": event.service,
                    "action": event.action,
                    "result": event.result,
                    "message": event.message,
                    "ts": event.ts.isoformat(),
                }
                for event in events
            ],
        }

    async def register_agent(
        self,
        *,
        agent_id: str,
        host: str,
            capabilities: dict[str, Any],
        version: str | None,
    ) -> dict[str, Any]:
        """Register an infra agent."""
        agent = await self._dao.upsert_agent(
            agent_id=agent_id,
            host=host,
            status=InfraAgentStatus.ONLINE,
            capabilities=capabilities,
            version=version,
        )
        return self._agent_payload(agent)

    async def heartbeat_agent(
        self,
        *,
        agent_id: str,
        host: str,
            capabilities: dict[str, Any],
        version: str | None,
        status: InfraAgentStatus,
    ) -> dict[str, Any]:
        """Update agent heartbeat."""
        agent = await self._dao.upsert_agent(
            agent_id=agent_id,
            host=host,
            status=status,
            capabilities=capabilities,
            version=version,
        )
        return self._agent_payload(agent)

    async def list_agents(self) -> list[dict[str, Any]]:
        """List registered agents."""
        return [self._agent_payload(agent) for agent in await self._dao.list_agents()]

    async def create_remote_apply_job(
        self,
        *,
        agent_id: str,
        signed_bundle: dict[str, Any],
        actor_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        """Create a remote apply contract job record."""
        job = await self._dao.create_apply_job(
            status=SystemApplyStatus.PENDING,
            target_host=agent_id,
            triggered_by=actor_id,
            change_set_json={"agent_id": agent_id, "bundle": signed_bundle},
            previous_snapshot_json=None,
        )
        await self._dao.append_apply_event(
            job.id,
            1,
            service="agent",
            action="agent.apply.accepted",
            result=SystemApplyEventResult.INFO,
            message=f"Remote apply accepted for agent '{agent_id}'.",
        )
        return {"accepted": True, "job_id": str(job.id), "agent_id": agent_id}

    @staticmethod
    def _agent_payload(agent: Any) -> dict[str, Any]:
        """Serialize agent model for API response."""
        return {
            "id": agent.id,
            "host": agent.host,
            "status": agent.status,
            "capabilities": agent.capabilities,
            "version": agent.version,
            "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
        }
