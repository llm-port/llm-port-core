"""Admin proxy endpoints for llm_port_rag internal APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from llm_port_backend.db.models.users import User
from llm_port_backend.services.rag.client import RagServiceClient, get_rag_client
from llm_port_backend.web.api.admin.rag.schema import (
    RagAdminRunCollectorResponseDTO,
    RagCollectorListResponseDTO,
    RagContainerDTO,
    RagContainerPayloadDTO,
    RagContainerTreeResponseDTO,
    RagDraftCreateRequestDTO,
    RagDraftDTO,
    RagDraftUpdateRequestDTO,
    RagIngestJobDTO,
    RagIngestJobListResponseDTO,
    RagKnowledgeSearchRequestDTO,
    RagKnowledgeSearchResponseDTO,
    RagPublishDTO,
    RagPublishListResponseDTO,
    RagPublishTriggerRequestDTO,
    RagPublishTriggerResponseDTO,
    RagRuntimeConfigResponse,
    RagRuntimeConfigUpdateRequest,
    RagUploadCompleteRequestDTO,
    RagUploadCompleteResponseDTO,
    RagUploadPresignRequestDTO,
    RagUploadPresignResponseDTO,
)
from llm_port_backend.web.api.rbac import require_permission, require_rag_container_action

router = APIRouter()


@router.get("/health")
async def rag_health(
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> dict[str, str]:
    """Check backend-to-rag connectivity."""
    payload = await rag_client.health()
    status_value = str(payload.get("status", "unknown"))
    return {"status": status_value}


@router.get("/runtime-config", response_model=RagRuntimeConfigResponse)
async def get_runtime_config(
    _user: Annotated[User, Depends(require_permission("rag.runtime", "read"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagRuntimeConfigResponse:
    """Get active RAG runtime config."""
    payload = await rag_client.get_runtime_config()
    return RagRuntimeConfigResponse.model_validate(payload)


@router.post("/runtime-config", response_model=RagRuntimeConfigResponse)
async def update_runtime_config(
    body: RagRuntimeConfigUpdateRequest,
    _user: Annotated[User, Depends(require_permission("rag.runtime", "update"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagRuntimeConfigResponse:
    """Update active runtime config in llm_port_rag."""
    payload = await rag_client.update_runtime_config(
        payload=body.payload.model_dump(mode="json"),
        embedding_secret=body.embedding_api_key,
    )
    return RagRuntimeConfigResponse.model_validate(payload)


@router.post("/knowledge/search", response_model=RagKnowledgeSearchResponseDTO)
async def search_knowledge(
    body: RagKnowledgeSearchRequestDTO,
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagKnowledgeSearchResponseDTO:
    """Proxy ACL-aware knowledge search."""
    payload = await rag_client.search_knowledge(body.model_dump(mode="json"))
    return RagKnowledgeSearchResponseDTO.model_validate(payload)


@router.get("/collectors", response_model=RagCollectorListResponseDTO)
async def list_collectors(
    _user: Annotated[User, Depends(require_permission("rag.jobs", "read"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagCollectorListResponseDTO:
    """List configured collectors."""
    payload = await rag_client.list_collectors()
    return RagCollectorListResponseDTO.model_validate(payload)


@router.post("/collectors/{collector_id}/run", response_model=RagAdminRunCollectorResponseDTO)
async def run_collector(
    collector_id: str,
    _user: Annotated[User, Depends(require_permission("rag.publish", "execute"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagAdminRunCollectorResponseDTO:
    """Trigger immediate collector run."""
    payload = await rag_client.run_collector(collector_id)
    return RagAdminRunCollectorResponseDTO.model_validate(payload)


@router.post("/containers", response_model=RagContainerDTO)
async def create_container(
    body: RagContainerPayloadDTO,
    _user: Annotated[User, Depends(require_permission("rag.containers", "create"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagContainerDTO:
    """Create virtual container."""
    payload = await rag_client.create_container(body.model_dump(mode="json"))
    return RagContainerDTO.model_validate(payload)


@router.get("/containers/tree", response_model=RagContainerTreeResponseDTO)
async def list_container_tree(
    tenant_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    _user: Annotated[User, Depends(require_permission("rag.containers", "read"))] = None,  # type: ignore[assignment]
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagContainerTreeResponseDTO:
    """List virtual container tree."""
    payload = await rag_client.list_containers_tree(tenant_id=tenant_id, workspace_id=workspace_id)
    return RagContainerTreeResponseDTO.model_validate(payload)


@router.patch("/containers/{container_id}", response_model=RagContainerDTO)
async def update_container(
    container_id: str,
    body: RagContainerPayloadDTO,
    _user: Annotated[User, Depends(require_permission("rag.containers", "update"))],
    _scoped: Annotated[User, Depends(require_rag_container_action("update", "container_id"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagContainerDTO:
    """Update virtual container."""
    payload = await rag_client.update_container(container_id, body.model_dump(mode="json"))
    return RagContainerDTO.model_validate(payload)


@router.delete("/containers/{container_id}", response_model=RagContainerDTO)
async def delete_container(
    container_id: str,
    _user: Annotated[User, Depends(require_permission("rag.containers", "delete"))],
    _scoped: Annotated[User, Depends(require_rag_container_action("delete", "container_id"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagContainerDTO:
    """Soft-delete virtual container."""
    payload = await rag_client.delete_container(container_id)
    return RagContainerDTO.model_validate(payload)


@router.post("/uploads/presign", response_model=RagUploadPresignResponseDTO)
async def create_upload_presign(
    body: RagUploadPresignRequestDTO,
    _user: Annotated[User, Depends(require_permission("rag.assets", "create"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagUploadPresignResponseDTO:
    """Create upload presign URL."""
    payload = await rag_client.create_upload_presign(body.model_dump(mode="json"))
    return RagUploadPresignResponseDTO.model_validate(payload)


@router.post("/uploads/complete", response_model=RagUploadCompleteResponseDTO)
async def complete_upload(
    body: RagUploadCompleteRequestDTO,
    _user: Annotated[User, Depends(require_permission("rag.assets", "create"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagUploadCompleteResponseDTO:
    """Complete uploaded object into draft operation."""
    payload = await rag_client.complete_upload(body.model_dump(mode="json"))
    return RagUploadCompleteResponseDTO.model_validate(payload)


@router.post("/drafts", response_model=RagDraftDTO)
async def create_draft(
    body: RagDraftCreateRequestDTO,
    _user: Annotated[User, Depends(require_permission("rag.assets", "update"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagDraftDTO:
    """Create draft."""
    payload = await rag_client.create_draft(body.model_dump(mode="json"))
    return RagDraftDTO.model_validate(payload)


@router.get("/drafts/{draft_id}", response_model=RagDraftDTO)
async def get_draft(
    draft_id: str,
    _user: Annotated[User, Depends(require_permission("rag.assets", "read"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagDraftDTO:
    """Get one draft."""
    payload = await rag_client.get_draft(draft_id)
    return RagDraftDTO.model_validate(payload)


@router.patch("/drafts/{draft_id}", response_model=RagDraftDTO)
async def patch_draft(
    draft_id: str,
    body: RagDraftUpdateRequestDTO,
    _user: Annotated[User, Depends(require_permission("rag.assets", "update"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagDraftDTO:
    """Patch one draft."""
    payload = await rag_client.patch_draft(draft_id, body.model_dump(mode="json"))
    return RagDraftDTO.model_validate(payload)


@router.post("/drafts/{draft_id}/publish", response_model=RagPublishTriggerResponseDTO)
async def publish_draft(
    draft_id: str,
    body: RagPublishTriggerRequestDTO,
    _user: Annotated[User, Depends(require_permission("rag.publish", "execute"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagPublishTriggerResponseDTO:
    """Trigger draft publish."""
    payload = await rag_client.publish_draft(draft_id, body.model_dump(mode="json"))
    return RagPublishTriggerResponseDTO.model_validate(payload)


@router.get("/publishes", response_model=RagPublishListResponseDTO)
async def list_publishes(
    limit: int = Query(default=100, ge=1, le=500),
    _user: Annotated[User, Depends(require_permission("rag.publish", "read"))] = None,  # type: ignore[assignment]
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagPublishListResponseDTO:
    """List publish requests."""
    payload = await rag_client.list_publishes(limit=limit)
    return RagPublishListResponseDTO.model_validate(payload)


@router.get("/publishes/{publish_id}", response_model=RagPublishDTO)
async def get_publish(
    publish_id: str,
    _user: Annotated[User, Depends(require_permission("rag.publish", "read"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagPublishDTO:
    """Get one publish request."""
    payload = await rag_client.get_publish(publish_id)
    return RagPublishDTO.model_validate(payload)


@router.get("/jobs", response_model=RagIngestJobListResponseDTO)
async def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    _user: Annotated[User, Depends(require_permission("rag.jobs", "read"))] = None,  # type: ignore[assignment]
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagIngestJobListResponseDTO:
    """List ingestion jobs."""
    payload = await rag_client.list_jobs(limit=limit)
    return RagIngestJobListResponseDTO.model_validate(payload)


@router.get("/jobs/{job_id}", response_model=RagIngestJobDTO)
async def get_job(
    job_id: str,
    _user: Annotated[User, Depends(require_permission("rag.jobs", "read"))],
    rag_client: RagServiceClient = Depends(get_rag_client),
) -> RagIngestJobDTO:
    """Get one ingestion job."""
    payload = await rag_client.get_job(job_id)
    return RagIngestJobDTO.model_validate(payload)
