"""Admin system control-plane endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette import status

from airgap_backend.db.dao.audit_dao import AuditDAO
from airgap_backend.db.dao.system_settings_dao import SystemSettingsDAO
from airgap_backend.db.models.containers import AuditResult
from airgap_backend.db.models.system_settings import InfraAgentStatus
from airgap_backend.db.models.users import User
from airgap_backend.services.docker.client import DockerService
from airgap_backend.services.system_settings import SettingsCrypto, SystemSettingsService
from airgap_backend.services.system_settings.executors import AgentApplyExecutor, LocalApplyExecutor
from airgap_backend.settings import settings
from airgap_backend.web.api.admin.dependencies import audit_action, get_root_mode_active
from airgap_backend.web.api.admin.system.schema import (
    AgentApplyRequest,
    AgentApplyResponse,
    AgentDTO,
    AgentHeartbeatRequest,
    AgentRegisterRequest,
    ApplyJobResponse,
    SettingsSchemaItemDTO,
    SettingsValuesResponse,
    SettingUpdateRequest,
    SettingUpdateResponse,
    WizardApplyRequest,
    WizardApplyResponse,
    WizardStepDTO,
    WizardStepsResponse,
)
from airgap_backend.web.api.rbac import require_permission

router = APIRouter()


def get_system_settings_service(
    request: Request,
    dao: SystemSettingsDAO = Depends(),
) -> SystemSettingsService:
    """Build request-scoped system settings service."""
    docker = getattr(request.app.state, "docker", None)
    if docker is None:
        docker = DockerService()
    crypto = SettingsCrypto(settings.settings_master_key)
    local_executor = LocalApplyExecutor(
        docker,
        compose_file=settings.system_compose_file,
    )
    agent_executor = AgentApplyExecutor()
    return SystemSettingsService(
        dao=dao,
        crypto=crypto,
        local_executor=local_executor,
        agent_executor=agent_executor,
        agent_enabled=settings.system_agent_enabled,
    )


@router.get("/settings/schema", response_model=list[SettingsSchemaItemDTO], name="system_settings_schema")
async def system_settings_schema(
    _user: Annotated[User, Depends(require_permission("system.settings", "read"))],
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> list[SettingsSchemaItemDTO]:
    """Return settings schema metadata for UI rendering."""
    return [SettingsSchemaItemDTO(**item) for item in service.schema()]


@router.get("/settings/values", response_model=SettingsValuesResponse, name="system_settings_values")
async def system_settings_values(
    _user: Annotated[User, Depends(require_permission("system.settings", "read"))],
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> SettingsValuesResponse:
    """Return effective settings values."""
    return SettingsValuesResponse(items=await service.list_values())


@router.put(
    "/settings/values/{key}",
    response_model=SettingUpdateResponse,
    name="system_settings_update",
)
async def system_settings_update(
    key: str,
    body: SettingUpdateRequest,
    user: Annotated[User, Depends(require_permission("system.settings", "update"))],
    root_mode_active: bool = Depends(get_root_mode_active),
    service: SystemSettingsService = Depends(get_system_settings_service),
    audit_dao: AuditDAO = Depends(),
) -> SettingUpdateResponse:
    """Update one setting and run immediate apply when required."""
    try:
        result = await service.update_value(
            key=key,
            value=body.value,
            actor_id=user.id,
            root_mode_active=root_mode_active,
            target_host=body.target_host,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    await audit_action(
        action="system.settings.update",
        target_type="system_setting",
        target_id=key,
        result=AuditResult.ALLOW if result.apply_status == "success" else AuditResult.DENY,
        actor_id=user.id,
        severity="high" if root_mode_active else "normal",
        audit_dao=audit_dao,
        metadata_json=(
            f'{{"apply_status":"{result.apply_status}","apply_scope":"{result.apply_scope}",'
            f'"apply_job_id":"{result.job_id}"}}'
        ),
    )

    return SettingUpdateResponse(
        key=key,
        apply_status=result.apply_status,
        apply_scope=result.apply_scope,
        apply_job_id=result.job_id,
        messages=result.messages,
    )


@router.get("/apply/{job_id}", response_model=ApplyJobResponse, name="system_apply_job")
async def system_apply_job(
    job_id: str,
    _user: Annotated[User, Depends(require_permission("system.apply", "read"))],
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> ApplyJobResponse:
    """Get apply job status and event timeline."""
    try:
        parsed = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid job id.") from exc
    payload = await service.get_apply_job(parsed)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apply job not found.")
    return ApplyJobResponse(**payload)


@router.get("/wizard/steps", response_model=WizardStepsResponse, name="system_wizard_steps")
async def wizard_steps(
    _user: Annotated[User, Depends(require_permission("system.wizard", "read"))],
) -> WizardStepsResponse:
    """Return static step layout for system initialization wizard."""
    return WizardStepsResponse(
        steps=[
            WizardStepDTO(
                id="host",
                title="Host Target",
                description="Select host/agent execution target.",
                setting_keys=[],
            ),
            WizardStepDTO(
                id="core-data",
                title="Core Data Services",
                description="Postgres and Redis core credentials.",
                setting_keys=["shared.postgres.password", "shared.redis.password"],
            ),
            WizardStepDTO(
                id="auth",
                title="Auth and Secrets",
                description="Gateway auth secret.",
                setting_keys=["llm_port_api.jwt_secret"],
            ),
            WizardStepDTO(
                id="gateway",
                title="LLM Gateway",
                description="Gateway and endpoint integration settings.",
                setting_keys=["api.server.endpoint_url", "api.server.container_name"],
            ),
            WizardStepDTO(
                id="observability",
                title="Langfuse / Grafana / Loki",
                description="Observability credentials and feature switches.",
                setting_keys=[
                    "llm_port_api.langfuse_enabled",
                    "llm_port_api.langfuse_host",
                    "llm_port_api.langfuse_public_key",
                    "llm_port_api.langfuse_secret_key",
                    "shared.grafana.admin_password",
                ],
            ),
            WizardStepDTO(
                id="verify",
                title="Health Verification",
                description="Confirm resulting service health after applies.",
                setting_keys=[],
            ),
        ]
    )


@router.post("/wizard/apply", response_model=WizardApplyResponse, name="system_wizard_apply")
async def wizard_apply(
    body: WizardApplyRequest,
    user: Annotated[User, Depends(require_permission("system.wizard", "execute"))],
    root_mode_active: bool = Depends(get_root_mode_active),
    service: SystemSettingsService = Depends(get_system_settings_service),
    audit_dao: AuditDAO = Depends(),
) -> WizardApplyResponse:
    """Apply a wizard step payload via shared settings path."""
    results: list[SettingUpdateResponse] = []
    for key, value in body.values.items():
        result = await service.update_value(
            key=key,
            value=value,
            actor_id=user.id,
            root_mode_active=root_mode_active,
            target_host=body.target_host,
        )
        results.append(
            SettingUpdateResponse(
                key=key,
                apply_status=result.apply_status,
                apply_scope=result.apply_scope,
                apply_job_id=result.job_id,
                messages=result.messages,
            ),
        )
    await audit_action(
        action="system.wizard.apply",
        target_type="system_wizard",
        target_id=body.target_host,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="high" if root_mode_active else "normal",
        audit_dao=audit_dao,
        metadata_json=f'{{"keys":{list(body.values.keys())}}}',
    )
    return WizardApplyResponse(results=results)


def _require_agent_token(request: Request) -> None:
    token = settings.system_agent_token
    if not token:
        return
    header = request.headers.get("Authorization", "")
    if header != f"Bearer {token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token.")


@router.post("/agents/register", response_model=AgentDTO, name="system_agent_register")
async def system_agent_register(
    body: AgentRegisterRequest,
    request: Request,
    _user: Annotated[User, Depends(require_permission("system.agents", "manage"))],
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> AgentDTO:
    """Register an infra agent."""
    _require_agent_token(request)
    payload = await service.register_agent(
        agent_id=body.id,
        host=body.host,
        capabilities=body.capabilities,
        version=body.version,
    )
    return AgentDTO(**payload)


@router.post("/agents/heartbeat", response_model=AgentDTO, name="system_agent_heartbeat")
async def system_agent_heartbeat(
    body: AgentHeartbeatRequest,
    request: Request,
    _user: Annotated[User, Depends(require_permission("system.agents", "manage"))],
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> AgentDTO:
    """Update infra agent heartbeat."""
    _require_agent_token(request)
    try:
        heartbeat_status = InfraAgentStatus(body.status)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid status",
        ) from exc
    payload = await service.heartbeat_agent(
        agent_id=body.id,
        host=body.host,
        capabilities=body.capabilities,
        version=body.version,
        status=heartbeat_status,
    )
    return AgentDTO(**payload)


@router.get("/agents", response_model=list[AgentDTO], name="system_agents_list")
async def system_agents_list(
    _user: Annotated[User, Depends(require_permission("system.agents", "read"))],
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> list[AgentDTO]:
    """List known infra agents."""
    return [AgentDTO(**item) for item in await service.list_agents()]


@router.post("/agents/{agent_id}/apply", response_model=AgentApplyResponse, name="system_agent_apply")
async def system_agent_apply(
    agent_id: str,
    body: AgentApplyRequest,
    _user: Annotated[User, Depends(require_permission("system.agents", "manage"))],
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> AgentApplyResponse:
    """Agent apply endpoint contract placeholder."""
    if not settings.system_agent_enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Remote agent execution is disabled.",
        )
    payload = await service.create_remote_apply_job(
        agent_id=agent_id,
        signed_bundle=body.signed_bundle,
        actor_id=None,
    )
    return AgentApplyResponse(**payload)


@router.get("/agents/{agent_id}/jobs/{job_id}", name="system_agent_job_status")
async def system_agent_job_status(
    agent_id: str,
    job_id: str,
    _user: Annotated[User, Depends(require_permission("system.agents", "read"))],
    service: SystemSettingsService = Depends(get_system_settings_service),
) -> dict[str, Any]:
    """Map agent job status endpoint to local apply jobs in v1."""
    _ = agent_id
    try:
        parsed = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid job id.") from exc
    payload = await service.get_apply_job(parsed)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apply job not found.")
    return payload
