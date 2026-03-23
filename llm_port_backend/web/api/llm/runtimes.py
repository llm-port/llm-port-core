"""LLM Runtime endpoints — create, list, get, start/stop/restart, delete, health, logs."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.llm_dao import ArtifactDAO, ModelDAO, ProviderDAO, RuntimeDAO
from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.node_control import NodeCommandStatus, NodeCommandType
from llm_port_backend.db.models.users import User
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.llm.service import LLMService
from llm_port_backend.services.nodes.service import NodeControlService
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.dependencies import audit_action, get_docker
from llm_port_backend.web.api.llm.dependencies import get_llm_service
from llm_port_backend.web.api.llm.schema import (
    RuntimeCreateRequest,
    RuntimeDTO,
    RuntimeHealthDTO,
    RuntimeUpdateRequest,
)
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()


def _get_node_control_service(
    request: Request,
    dao: NodeControlDAO = Depends(),
) -> NodeControlService:
    llm_service = getattr(request.app.state, "llm_service", None)
    gateway_sync = getattr(llm_service, "gateway_sync", None)
    return NodeControlService(
        dao=dao,
        pepper=settings.settings_master_key,
        enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
        default_command_timeout_sec=settings.node_command_default_timeout_sec,
        gateway_sync=gateway_sync,
    )


@router.get("/", response_model=list[RuntimeDTO])
async def list_runtimes(
    user: User = Depends(require_permission("llm.runtimes", "read")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
) -> list[RuntimeDTO]:
    """List all runtimes (with container-state reconciliation)."""
    runtimes = await llm_service.reconcile_all_runtimes(runtime_dao)
    return [RuntimeDTO.model_validate(r) for r in runtimes]


@router.post("/", response_model=RuntimeDTO, status_code=status.HTTP_201_CREATED)
async def create_runtime(
    body: RuntimeCreateRequest,
    user: User = Depends(require_permission("llm.runtimes", "create")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    provider_dao: ProviderDAO = Depends(),
    model_dao: ModelDAO = Depends(),
    artifact_dao: ArtifactDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RuntimeDTO:
    """Create a new runtime, validate compatibility, and start the container."""
    try:
        runtime = await llm_service.create_runtime(
            runtime_dao,
            provider_dao,
            model_dao,
            artifact_dao,
            name=body.name,
            provider_id=body.provider_id,
            model_id=body.model_id,
            generic_config=body.generic_config,
            provider_config=body.provider_config,
            openai_compat=body.openai_compat,
            target_node_id=body.target_node_id,
            placement_hints=body.placement_hints,
            model_source=body.model_source,
            image_source=body.image_source,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to start runtime container: {exc}",
        ) from exc
    await audit_action(
        action="llm.runtime.create",
        target_type="llm_runtime",
        target_id=str(runtime.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return RuntimeDTO.model_validate(runtime)


@router.get("/{runtime_id}", response_model=RuntimeDTO)
async def get_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "read")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
) -> RuntimeDTO:
    """Get a single runtime by ID (with container-state reconciliation)."""
    runtime = await runtime_dao.get(runtime_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    runtime = await llm_service.reconcile_runtime_status(runtime_dao, runtime)
    return RuntimeDTO.model_validate(runtime)


@router.post("/{runtime_id}/start", response_model=RuntimeDTO)
async def start_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "start")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RuntimeDTO:
    """Start a stopped runtime."""
    try:
        runtime = await llm_service.start_runtime(runtime_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_action(
        action="llm.runtime.start",
        target_type="llm_runtime",
        target_id=str(runtime_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return RuntimeDTO.model_validate(runtime)


@router.post("/{runtime_id}/stop", response_model=RuntimeDTO)
async def stop_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "stop")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RuntimeDTO:
    """Stop a running runtime."""
    try:
        runtime = await llm_service.stop_runtime(runtime_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_action(
        action="llm.runtime.stop",
        target_type="llm_runtime",
        target_id=str(runtime_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return RuntimeDTO.model_validate(runtime)


@router.post("/{runtime_id}/restart", response_model=RuntimeDTO)
async def restart_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "restart")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RuntimeDTO:
    """Restart a runtime."""
    try:
        runtime = await llm_service.restart_runtime(runtime_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_action(
        action="llm.runtime.restart",
        target_type="llm_runtime",
        target_id=str(runtime_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return RuntimeDTO.model_validate(runtime)


@router.delete("/{runtime_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "delete")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Stop the container and delete the runtime record."""
    try:
        await llm_service.delete_runtime(runtime_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit_action(
        action="llm.runtime.delete",
        target_type="llm_runtime",
        target_id=str(runtime_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )


@router.patch("/{runtime_id}", response_model=RuntimeDTO)
async def update_runtime(
    runtime_id: uuid.UUID,
    body: RuntimeUpdateRequest,
    user: User = Depends(require_permission("llm.runtimes", "update")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    provider_dao: ProviderDAO = Depends(),
    model_dao: ModelDAO = Depends(),
    artifact_dao: ArtifactDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RuntimeDTO:
    """Update runtime config and rebuild the container."""
    try:
        runtime = await llm_service.update_and_restart_runtime(
            runtime_dao,
            provider_dao,
            model_dao,
            artifact_dao,
            runtime_id,
            name=body.name,
            generic_config=body.generic_config,
            provider_config=body.provider_config,
            openai_compat=body.openai_compat,
            target_node_id=body.target_node_id,
            placement_hints=body.placement_hints,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to rebuild runtime container: {exc}",
        ) from exc
    await audit_action(
        action="llm.runtime.update",
        target_type="llm_runtime",
        target_id=str(runtime_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return RuntimeDTO.model_validate(runtime)


@router.get("/{runtime_id}/health", response_model=RuntimeHealthDTO)
async def runtime_health(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "read")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    provider_dao: ProviderDAO = Depends(),
) -> RuntimeHealthDTO:
    """Probe a runtime's health."""
    try:
        result = await llm_service.get_runtime_health(runtime_dao, provider_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RuntimeHealthDTO(**result)


@router.get("/{runtime_id}/logs", response_class=StreamingResponse)
async def runtime_logs(
    runtime_id: uuid.UUID,
    tail: int = 200,
    follow: bool = False,
    user: User = Depends(require_permission("llm.runtimes", "read")),
    runtime_dao: RuntimeDAO = Depends(),
    docker: DockerService = Depends(get_docker),
    node_service: NodeControlService = Depends(_get_node_control_service),
):
    """Stream logs from the runtime's container (local or remote node)."""
    runtime = await runtime_dao.get(runtime_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    if not runtime.container_ref:
        raise HTTPException(status_code=400, detail="Runtime has no container")

    # Remote node runtime — fetch logs via node command
    if runtime.container_ref.startswith("node:"):
        node_id_str = runtime.assigned_node_id
        if not node_id_str:
            raise HTTPException(status_code=400, detail="Runtime assigned to node but no node_id")
        command = await node_service.issue_command(
            node_id=uuid.UUID(str(node_id_str)),
            command_type=NodeCommandType.FETCH_CONTAINER_LOGS.value,
            payload={"runtime_id": str(runtime_id), "runtime_name": runtime.name, "tail": tail},
            issued_by=user.id,
            correlation_id=f"logs-{runtime_id}",
            timeout_sec=30,
            idempotency_key=None,  # always fresh
        )
        # Commit so the WebSocket handler's session can see the new command
        command_id = command.id
        await node_service._dao.session.commit()
        # Poll for result (agent typically responds in <2s)
        for _ in range(15):
            await asyncio.sleep(1)
            # Expire cached state so we see changes from the WebSocket handler's session
            node_service._dao.session.expire_all()
            cmd = await node_service.get_command(command_id=command_id)
            if cmd and cmd.status in (
                NodeCommandStatus.SUCCEEDED.value,
                NodeCommandStatus.FAILED.value,
                NodeCommandStatus.TIMED_OUT.value,
            ):
                if cmd.status == NodeCommandStatus.SUCCEEDED.value:
                    logs_text = (cmd.result_json or {}).get("logs", "")
                    return PlainTextResponse(logs_text)
                detail = cmd.error_message or "Failed to fetch logs from node agent."
                raise HTTPException(status_code=502, detail=detail)
        raise HTTPException(status_code=504, detail="Timed out waiting for node agent to return logs.")

    # Local container — stream directly from Docker
    async def _stream():  # type: ignore[return]
        async for line in docker.logs(runtime.container_ref, tail=tail, follow=follow):
            yield line

    return StreamingResponse(_stream(), media_type="text/plain")
