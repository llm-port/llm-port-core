"""Admin system control-plane endpoints."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.dao.system_settings_dao import SystemSettingsDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.system_settings import InfraAgentStatus
from llm_port_backend.db.models.users import User
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.nodes import NodeControlService
from llm_port_backend.services.system_settings import SettingsCrypto, SystemSettingsService
from llm_port_backend.services.system_settings.executors import AgentApplyExecutor, LocalApplyExecutor
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.hardware.schema import (
    GpuDeviceDTO,
    GpuInventoryDTO,
    GpuMetricsDTO,
    HardwareDTO,
    VllmImagePresetDTO,
)
from llm_port_backend.web.api.admin.hardware.views import _build_presets, _VLLM_IMAGES
from llm_port_backend.web.api.admin.dependencies import audit_action, get_root_mode_active
from llm_port_backend.web.api.admin.system.schema import (
    AgentApplyRequest,
    AgentApplyResponse,
    AgentDTO,
    AgentHeartbeatRequest,
    AgentRegisterRequest,
    ApplyJobResponse,
    GrafanaWebhookPayloadDTO,
    GrafanaWebhookResponseDTO,
    NodeCommandDTO,
    NodeProfileAssignRequest,
    NodeProfileCreateRequest,
    NodeProfileDTO,
    NodeProfileUpdateRequest,
    NodeCommandIssueRequest,
    NodeCommandTimelineDTO,
    NodeDTO,
    NodeDrainRequest,
    NodeEnrollRequest,
    NodeEnrollResponse,
    NodeEnrollmentTokenCreateRequest,
    NodeEnrollmentTokenCreateResponse,
    NodeMaintenanceRequest,
    NodeRotateCredentialResponse,
    SettingsSchemaItemDTO,
    SettingsValuesResponse,
    SettingUpdateRequest,
    SettingUpdateResponse,
    WizardApplyRequest,
    WizardApplyResponse,
    WizardStepDTO,
    WizardStepsResponse,
)
from llm_port_backend.web.api.rbac import require_permission

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


def get_node_control_service(
    request: Request,
    dao: NodeControlDAO = Depends(),
) -> NodeControlService:
    """Build request-scoped node control service."""
    llm_service = getattr(request.app.state, "llm_service", None)
    gateway_sync = getattr(llm_service, "gateway_sync", None)
    return NodeControlService(
        dao=dao,
        pepper=settings.settings_master_key,
        enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
        default_command_timeout_sec=settings.node_command_default_timeout_sec,
        gateway_sync=gateway_sync,
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
                description="JWT signing/verification secrets.",
                setting_keys=["llm_port_backend.users_secret", "llm_port_api.jwt_secret"],
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
                id="pii",
                title="PII Protection",
                description="Enable PII detection and configure redaction policy.",
                setting_keys=[
                    "llm_port_api.pii_service_url",
                    "llm_port_api.pii_default_policy",
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


@router.post(
    "/nodes/enrollment-tokens",
    response_model=NodeEnrollmentTokenCreateResponse,
    name="system_node_enrollment_token_create",
)
async def system_node_enrollment_token_create(
    body: NodeEnrollmentTokenCreateRequest,
    user: Annotated[User, Depends(require_permission("system.nodes", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeEnrollmentTokenCreateResponse:
    """Create one-time enrollment token for a node agent."""
    payload = await service.create_enrollment_token(issued_by=user.id, note=body.note)
    return NodeEnrollmentTokenCreateResponse(**payload)


@router.post("/nodes/enroll", response_model=NodeEnrollResponse, name="system_node_enroll")
async def system_node_enroll(
    body: NodeEnrollRequest,
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeEnrollResponse:
    """Exchange enrollment token for node credentials."""
    try:
        payload = await service.enroll_node(
            enrollment_token=body.enrollment_token,
            agent_id=body.agent_id,
            host=body.host,
            capabilities=body.capabilities,
            version=body.version,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return NodeEnrollResponse(**payload)


@router.post(
    "/nodes/credentials/rotate",
    response_model=NodeRotateCredentialResponse,
    name="system_node_rotate_credential",
)
async def system_node_rotate_credential(
    request: Request,
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeRotateCredentialResponse:
    """Rotate node credential using current credential bearer token."""
    try:
        payload = await service.rotate_credential(authorization=request.headers.get("Authorization"))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return NodeRotateCredentialResponse(**payload)


@router.get("/nodes", response_model=list[NodeDTO], name="system_nodes_list")
async def system_nodes_list(
    _user: Annotated[User, Depends(require_permission("system.nodes", "read"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> list[NodeDTO]:
    """List managed nodes."""
    return [NodeDTO(**item) for item in await service.list_nodes()]


@router.get("/nodes/{node_id}", response_model=NodeDTO, name="system_node_get")
async def system_node_get(
    node_id: str,
    _user: Annotated[User, Depends(require_permission("system.nodes", "read"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeDTO:
    """Get one node with latest inventory/utilization snapshot."""
    try:
        parsed = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid node id.") from exc
    payload = await service.get_node(node_id=parsed)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    return NodeDTO(**payload)


@router.get(
    "/nodes/{node_id}/hardware",
    response_model=HardwareDTO,
    name="system_node_hardware",
    summary="Get GPU hardware info for a specific node",
)
async def system_node_hardware(
    node_id: str,
    _user: Annotated[User, Depends(require_permission("system.nodes", "read"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> HardwareDTO:
    """Return GPU inventory and vLLM image presets for a remote node.

    Transforms the node agent's ``latest_inventory`` into the same
    ``HardwareDTO`` schema used by the local ``/admin/hardware``
    endpoint so the provider wizard can treat local and node
    deployments identically.
    """
    try:
        parsed = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid node id.") from exc

    payload = await service.get_node(node_id=parsed)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    inventory: dict[str, Any] = payload.get("latest_inventory") or {}
    utilization: dict[str, Any] = payload.get("latest_utilization") or {}
    gpu_raw: dict[str, Any] = inventory.get("gpu") or {}

    gpu_count: int = int(gpu_raw.get("count", 0))
    total_vram_bytes: int = int(gpu_raw.get("total_vram_bytes", 0))
    has_gpu = gpu_count > 0

    # Build device list from node agent format → GpuDeviceDTO
    devices: list[GpuDeviceDTO] = []
    for idx, dev in enumerate(gpu_raw.get("devices") or []):
        vram_mib = int(dev.get("memory_total_mib", 0))
        devices.append(
            GpuDeviceDTO(
                index=idx,
                vendor="nvidia",  # node agent only detects NVIDIA via nvidia-smi
                model=dev.get("model", f"GPU {idx}"),
                vram_bytes=vram_mib * 1024 * 1024,
                driver_version=dev.get("driver_version", ""),
                compute_api="cuda",
            ),
        )

    primary_vendor = "nvidia" if has_gpu else "unknown"
    primary_compute_api = "cuda" if has_gpu else "unknown"

    gpu_dto = GpuInventoryDTO(
        devices=devices,
        primary_vendor=primary_vendor,
        primary_compute_api=primary_compute_api,
        has_gpu=has_gpu,
        device_count=gpu_count,
        total_vram_bytes=total_vram_bytes,
    )

    # Utilization metrics from latest snapshot
    gpu_util_raw: dict[str, Any] = utilization.get("gpu") or {}
    used_vram: int | None = int(gpu_util_raw.get("used_vram_bytes", 0)) if has_gpu else None
    avg_util: float | None = None
    if has_gpu:
        utils = [
            d.get("utilization_pct")
            for d in (gpu_util_raw.get("devices") or [])
            if d.get("utilization_pct") is not None
        ]
        if utils:
            avg_util = sum(utils) / len(utils)

    metrics_dto = GpuMetricsDTO(
        util_percent=avg_util,
        vram_used_bytes=used_vram,
        vram_total_bytes=total_vram_bytes if has_gpu else None,
    )

    recommended_image = _VLLM_IMAGES.get(primary_vendor)
    presets = _build_presets(primary_vendor)

    return HardwareDTO(
        gpu=gpu_dto,
        gpu_metrics=metrics_dto,
        recommended_vllm_image=recommended_image,
        legacy_vllm_image=settings.default_vllm_legacy_image,
        vllm_image_presets=presets,
    )


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT, name="system_node_delete")
async def system_node_delete(
    node_id: str,
    _user: Annotated[User, Depends(require_permission("system.nodes", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> None:
    """Delete a registered node and all its associated data."""
    try:
        parsed = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid node id.") from exc
    deleted = await service.delete_node(node_id=parsed)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")


@router.post("/nodes/{node_id}/maintenance", response_model=NodeDTO, name="system_node_maintenance")
async def system_node_maintenance(
    node_id: str,
    body: NodeMaintenanceRequest,
    user: Annotated[User, Depends(require_permission("system.node_maintenance", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeDTO:
    """Toggle maintenance mode for one node."""
    try:
        parsed = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid node id.") from exc
    try:
        payload = await service.set_node_maintenance(
            node_id=parsed,
            enabled=body.enabled,
            reason=body.reason,
            requested_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NodeDTO(**payload)


@router.post("/nodes/{node_id}/drain", response_model=NodeDTO, name="system_node_drain")
async def system_node_drain(
    node_id: str,
    body: NodeDrainRequest,
    _user: Annotated[User, Depends(require_permission("system.node_maintenance", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeDTO:
    """Toggle draining state for one node."""
    try:
        parsed = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid node id.") from exc
    try:
        payload = await service.set_node_draining(node_id=parsed, enabled=body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NodeDTO(**payload)


@router.post("/nodes/{node_id}/commands", response_model=NodeCommandDTO, name="system_node_command_issue")
async def system_node_command_issue(
    node_id: str,
    body: NodeCommandIssueRequest,
    user: Annotated[User, Depends(require_permission("system.node_commands", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeCommandDTO:
    """Issue a command to one node."""
    try:
        parsed = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid node id.") from exc
    try:
        command = await service.issue_command(
            node_id=parsed,
            command_type=body.command_type,
            payload=body.payload,
            issued_by=user.id,
            correlation_id=body.correlation_id,
            timeout_sec=body.timeout_sec,
            idempotency_key=body.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NodeCommandDTO(**service.serialize_command(command))


@router.get("/nodes/{node_id}/commands", response_model=list[NodeCommandDTO], name="system_node_commands_list")
async def system_node_commands_list(
    node_id: str,
    _user: Annotated[User, Depends(require_permission("system.node_commands", "read"))],
    dao: NodeControlDAO = Depends(),
    service: NodeControlService = Depends(get_node_control_service),
) -> list[NodeCommandDTO]:
    """List commands for one node."""
    try:
        parsed = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid node id.") from exc
    rows = await dao.list_node_commands(node_id=parsed)
    return [NodeCommandDTO(**service.serialize_command(item)) for item in rows]


@router.get(
    "/nodes/{node_id}/commands/{command_id}",
    response_model=NodeCommandTimelineDTO,
    name="system_node_command_timeline",
)
async def system_node_command_timeline(
    node_id: str,
    command_id: str,
    _user: Annotated[User, Depends(require_permission("system.node_commands", "read"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeCommandTimelineDTO:
    """Get one command timeline."""
    try:
        parsed_node = uuid.UUID(node_id)
        parsed_command = uuid.UUID(command_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id.") from exc
    payload = await service.get_command_timeline(command_id=parsed_command)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found.")
    command_node_id = payload.get("command", {}).get("node_id")
    if command_node_id != str(parsed_node):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found.")
    return NodeCommandTimelineDTO(**payload)


@router.websocket("/nodes/stream")
async def system_node_stream(websocket: WebSocket) -> None:
    """Persistent outbound stream channel used by node agents."""
    session_factory = getattr(websocket.app.state, "db_session_factory", None)
    if session_factory is None:
        await websocket.close(code=1011)
        return

    session = session_factory()
    session.expire_on_commit = False
    dao = NodeControlDAO(session)
    service = NodeControlService(
        dao=dao,
        pepper=settings.settings_master_key,
        enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
        default_command_timeout_sec=settings.node_command_default_timeout_sec,
        gateway_sync=getattr(getattr(websocket.app.state, "llm_service", None), "gateway_sync", None),
    )
    stream_session = None
    try:
        node, credential = await service.authenticate_agent(
            authorization=websocket.headers.get("authorization"),
        )
    except PermissionError:
        await websocket.close(code=4401)
        await session.close()
        return

    _node_id = node.id  # cache before any commit may expire the object
    # Capture the real client IP for host updates.
    # Prefer X-Forwarded-For > websocket.client.host, but the
    # agent-reported advertise_host (sent in heartbeats) takes
    # final precedence — see heartbeat handling below.
    _client_ip: str | None = None
    if websocket.client:
        _client_ip = websocket.client.host
    forwarded = websocket.headers.get("x-forwarded-for")
    if forwarded:
        _client_ip = forwarded.split(",")[0].strip()
    await websocket.accept()
    try:
        # Don't overwrite node.host on connect; wait for the first
        # heartbeat which carries advertise_host from the agent.
        stream_session = await service.create_stream_session(node=node, credential=credential)
        commands = await service.list_commands_for_dispatch(node_id=_node_id)
        profile_payload = await service.get_node_profile(node_id=_node_id)
        await websocket.send_json(
            {
                "type": "hello_ack",
                "session_id": str(stream_session.id),
                "node_id": str(node.id),
                "commands": commands,
                "profile": profile_payload,
            },
        )
        await session.commit()

        idle_timeout_sec = max(settings.node_stream_idle_timeout_sec, 5)
        while True:
            try:
                payload = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=idle_timeout_sec,
                )
            except asyncio.TimeoutError:
                await websocket.close(code=4408, reason="Idle timeout")
                break

            seq_raw = payload.get("seq")
            if isinstance(seq_raw, int):
                accepted = await service.update_stream_offset(
                    session=stream_session,
                    offset=seq_raw,
                )
                if not accepted:
                    continue

            message_type = str(payload.get("type") or "").strip().lower()
            if message_type == "heartbeat":
                status_value = str(payload.get("status") or "healthy")
                capabilities_payload = payload.get("capabilities")
                capabilities = capabilities_payload if isinstance(capabilities_payload, dict) else None
                version = payload.get("version") if isinstance(payload.get("version"), str) else None
                # Prefer agent-reported advertise_host over connection IP
                _host_for_hb = _client_ip
                adv = payload.get("advertise_host")
                if isinstance(adv, str) and adv.strip():
                    _host_for_hb = adv.strip()
                await service.heartbeat_node(
                    node=node,
                    status=status_value,
                    capabilities=capabilities,
                    version=version,
                    host=_host_for_hb,
                )
            elif message_type == "inventory":
                inventory_payload = payload.get("inventory")
                utilization_payload = payload.get("utilization")
                inventory = inventory_payload if isinstance(inventory_payload, dict) else {}
                utilization = utilization_payload if isinstance(utilization_payload, dict) else {}
                await service.record_inventory(node=node, inventory=inventory, utilization=utilization)
            elif message_type == "command_ack":
                try:
                    command_id = uuid.UUID(str(payload.get("command_id")))
                except ValueError:
                    continue
                await service.record_command_ack(node_id=node.id, command_id=command_id, payload=payload)
            elif message_type == "command_progress":
                try:
                    command_id = uuid.UUID(str(payload.get("command_id")))
                except ValueError:
                    continue
                await service.record_command_progress(node_id=node.id, command_id=command_id, payload=payload)
            elif message_type == "command_result":
                try:
                    command_id = uuid.UUID(str(payload.get("command_id")))
                except ValueError:
                    continue
                await service.record_command_result(node_id=node.id, command_id=command_id, payload=payload)
            elif message_type == "event_batch":
                events = payload.get("events")
                if isinstance(events, list):
                    normalized = [item for item in events if isinstance(item, dict)]
                    if normalized:
                        await service.record_node_events(node_id=node.id, events=normalized)

            dispatch_items = await service.list_commands_for_dispatch(node_id=node.id)
            if dispatch_items:
                await websocket.send_json({"type": "commands", "items": dispatch_items})
            await session.commit()
    except WebSocketDisconnect:
        logging.getLogger(__name__).info("Node agent %s disconnected from stream.", _node_id)
        await session.rollback()
    except Exception:
        logging.getLogger(__name__).exception("Node stream error for node %s.", _node_id)
        await session.rollback()
        raise
    finally:
        if stream_session is not None:
            try:
                await service.close_stream_session(session=stream_session)
                await session.commit()
            except Exception:
                await session.rollback()
        await session.close()


@router.post(
    "/alerts/grafana/webhook",
    response_model=GrafanaWebhookResponseDTO,
    name="system_grafana_webhook",
)
async def system_grafana_webhook(
    body: GrafanaWebhookPayloadDTO,
    request: Request,
) -> GrafanaWebhookResponseDTO:
    """Ingest optional Grafana alert webhooks and enqueue admin alerts.

    This is an Enterprise-only endpoint.  Returns ``402 Payment Required``
    unless the Observability Pro plugin is loaded (which shadows this route).
    """
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=(
            "Grafana webhook alerting requires the Observability Pro plugin. "
            "Install llm-port-ee to enable this endpoint."
        ),
    )


# ── Node profiles ──────────────────────────────────────────


@router.post("/node-profiles", response_model=NodeProfileDTO, status_code=status.HTTP_201_CREATED, name="system_node_profile_create")
async def system_node_profile_create(
    body: NodeProfileCreateRequest,
    _user: Annotated[User, Depends(require_permission("system.node_profiles", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeProfileDTO:
    """Create a node profile."""
    payload = await service.create_profile(
        name=body.name,
        description=body.description,
        is_default=body.is_default,
        runtime_config=body.runtime_config,
        gpu_config=body.gpu_config,
        storage_config=body.storage_config,
        network_config=body.network_config,
        logging_config=body.logging_config,
        security_config=body.security_config,
        update_config=body.update_config,
    )
    return NodeProfileDTO(**payload)


@router.get("/node-profiles", response_model=list[NodeProfileDTO], name="system_node_profiles_list")
async def system_node_profiles_list(
    _user: Annotated[User, Depends(require_permission("system.node_profiles", "read"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> list[NodeProfileDTO]:
    """List all node profiles."""
    return [NodeProfileDTO(**p) for p in await service.list_profiles()]


@router.get("/node-profiles/{profile_id}", response_model=NodeProfileDTO, name="system_node_profile_get")
async def system_node_profile_get(
    profile_id: str,
    _user: Annotated[User, Depends(require_permission("system.node_profiles", "read"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeProfileDTO:
    """Get a node profile."""
    try:
        parsed = uuid.UUID(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid profile id.") from exc
    payload = await service.get_profile(profile_id=parsed)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found.")
    return NodeProfileDTO(**payload)


@router.put("/node-profiles/{profile_id}", response_model=NodeProfileDTO, name="system_node_profile_update")
async def system_node_profile_update(
    profile_id: str,
    body: NodeProfileUpdateRequest,
    _user: Annotated[User, Depends(require_permission("system.node_profiles", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeProfileDTO:
    """Update a node profile."""
    try:
        parsed = uuid.UUID(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid profile id.") from exc
    updates = body.model_dump(exclude_unset=True)
    try:
        payload = await service.update_profile(profile_id=parsed, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NodeProfileDTO(**payload)


@router.delete("/node-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT, name="system_node_profile_delete")
async def system_node_profile_delete(
    profile_id: str,
    _user: Annotated[User, Depends(require_permission("system.node_profiles", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> None:
    """Delete a node profile."""
    try:
        parsed = uuid.UUID(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid profile id.") from exc
    try:
        await service.delete_profile(profile_id=parsed)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/nodes/{node_id}/profile", response_model=NodeDTO, name="system_node_profile_assign")
async def system_node_profile_assign(
    node_id: str,
    body: NodeProfileAssignRequest,
    _user: Annotated[User, Depends(require_permission("system.node_profiles", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeDTO:
    """Assign a profile to a node."""
    try:
        parsed_node = uuid.UUID(node_id)
        parsed_profile = uuid.UUID(body.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id.") from exc
    try:
        payload = await service.assign_profile_to_node(node_id=parsed_node, profile_id=parsed_profile)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NodeDTO(**payload)


@router.delete("/nodes/{node_id}/profile", response_model=NodeDTO, name="system_node_profile_unassign")
async def system_node_profile_unassign(
    node_id: str,
    _user: Annotated[User, Depends(require_permission("system.node_profiles", "manage"))],
    service: NodeControlService = Depends(get_node_control_service),
) -> NodeDTO:
    """Unassign the profile from a node."""
    try:
        parsed = uuid.UUID(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid node id.") from exc
    try:
        payload = await service.unassign_profile_from_node(node_id=parsed)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return NodeDTO(**payload)
