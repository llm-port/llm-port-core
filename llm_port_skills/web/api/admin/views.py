"""Admin API views for Skills CRUD, versioning, and assignments."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_skills.db.session import get_db_session
from llm_port_skills.services.dao import SkillDao
from llm_port_skills.services.frontmatter import (
    compose_skill_document,
    extract_metadata_from_frontmatter,
    parse_skill_document,
)
from llm_port_skills.web.api.admin.schemas import (
    CompletionsResponse,
    CreateAssignmentRequest,
    CreateSkillRequest,
    SkillAssignmentResponse,
    SkillDetailResponse,
    SkillListResponse,
    SkillSummaryResponse,
    SkillVersionResponse,
    UpdateSkillBodyRequest,
    UpdateSkillMetadataRequest,
)
from llm_port_skills.web.api.auth import AuthContext, get_auth_context

router = APIRouter()


# ── Skill CRUD ────────────────────────────────────────────────────────


@router.get("", response_model=SkillListResponse)
async def list_skills(
    tenant_id: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillListResponse:
    """List skills for the current tenant."""
    effective_tenant = tenant_id or auth.tenant_id
    dao = SkillDao(session)
    items, total = await dao.list_skills(
        effective_tenant,
        status=status,
        scope=scope,
        tag=tag,
        search=search,
        offset=offset,
        limit=min(limit, 100),
    )
    return SkillListResponse(
        items=[SkillSummaryResponse.model_validate(s) for s in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=SkillDetailResponse, status_code=201)
async def create_skill(
    body: CreateSkillRequest,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Create a new skill from a frontmatter+markdown document."""
    parsed = parse_skill_document(body.content)
    metadata = extract_metadata_from_frontmatter(parsed.frontmatter)

    name = body.name or metadata.get("name")
    metadata.pop("name", None)
    if not name:
        raise HTTPException(
            status_code=422,
            detail="Skill name is required (in frontmatter or request body).",
        )

    try:
        skill = await SkillDao(session).create_skill(
            auth.tenant_id,
            name=name,
            body_markdown=parsed.body,
            frontmatter_yaml=parsed.raw_frontmatter or None,
            created_by=auth.user_id,
            **metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _skill_to_detail(skill)


@router.get("/{skill_id}", response_model=SkillDetailResponse)
async def get_skill(
    skill_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Get full skill detail with current version body."""
    dao = SkillDao(session)
    skill = await dao.get_skill(skill_id, auth.tenant_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found.")
    return _skill_to_detail(skill)


@router.put("/{skill_id}", response_model=SkillDetailResponse)
async def update_skill_metadata(
    skill_id: uuid.UUID,
    body: UpdateSkillMetadataRequest,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Update skill metadata fields."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update.")
    dao = SkillDao(session)
    skill = await dao.update_skill_metadata(skill_id, auth.tenant_id, **updates)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found.")
    return _skill_to_detail(skill)


@router.put("/{skill_id}/body", response_model=SkillVersionResponse)
async def update_skill_body(
    skill_id: uuid.UUID,
    body: UpdateSkillBodyRequest,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillVersionResponse:
    """Update the skill body content (creates a new version)."""
    parsed = parse_skill_document(body.content)
    metadata = extract_metadata_from_frontmatter(parsed.frontmatter)

    dao = SkillDao(session)
    # Update metadata from frontmatter if present
    if metadata:
        await dao.update_skill_metadata(skill_id, auth.tenant_id, **metadata)

    version = await dao.update_skill_body(
        skill_id,
        auth.tenant_id,
        body_markdown=parsed.body,
        frontmatter_yaml=parsed.raw_frontmatter or None,
        change_note=body.change_note,
        created_by=auth.user_id,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Skill not found.")
    return SkillVersionResponse.model_validate(version)


@router.post("/{skill_id}/publish", response_model=SkillDetailResponse)
async def publish_skill(
    skill_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Publish a skill (set status to published)."""
    dao = SkillDao(session)
    try:
        skill = await dao.set_status(skill_id, auth.tenant_id, "published")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found.")
    return _skill_to_detail(skill)


@router.post("/{skill_id}/archive", response_model=SkillDetailResponse)
async def archive_skill(
    skill_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Archive a skill."""
    dao = SkillDao(session)
    skill = await dao.set_status(skill_id, auth.tenant_id, "archived")
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found.")
    return _skill_to_detail(skill)


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a skill and all its versions/assignments."""
    dao = SkillDao(session)
    deleted = await dao.delete_skill(skill_id, auth.tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Skill not found.")


# ── Versions ──────────────────────────────────────────────────────────


@router.get(
    "/{skill_id}/versions",
    response_model=list[SkillVersionResponse],
)
async def list_versions(
    skill_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[SkillVersionResponse]:
    """List all versions for a skill."""
    dao = SkillDao(session)
    skill = await dao.get_skill(skill_id, auth.tenant_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found.")
    versions = await dao.list_versions(skill_id)
    return [SkillVersionResponse.model_validate(v) for v in versions]


@router.get(
    "/{skill_id}/versions/{version}",
    response_model=SkillVersionResponse,
)
async def get_version(
    skill_id: uuid.UUID,
    version: int,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillVersionResponse:
    """Get a specific version of a skill."""
    dao = SkillDao(session)
    skill = await dao.get_skill(skill_id, auth.tenant_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found.")
    ver = await dao.get_version(skill_id, version)
    if ver is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    return SkillVersionResponse.model_validate(ver)


# ── Assignments ───────────────────────────────────────────────────────


@router.get(
    "/{skill_id}/assignments",
    response_model=list[SkillAssignmentResponse],
)
async def list_assignments(
    skill_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[SkillAssignmentResponse]:
    """List all assignments for a skill."""
    dao = SkillDao(session)
    skill = await dao.get_skill(skill_id, auth.tenant_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found.")
    assignments = await dao.list_assignments(skill_id)
    return [SkillAssignmentResponse.model_validate(a) for a in assignments]


@router.post(
    "/{skill_id}/assign",
    response_model=SkillAssignmentResponse,
    status_code=201,
)
async def create_assignment(
    skill_id: uuid.UUID,
    body: CreateAssignmentRequest,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillAssignmentResponse:
    """Create a new skill assignment."""
    dao = SkillDao(session)
    skill = await dao.get_skill(skill_id, auth.tenant_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found.")
    try:
        assignment = await dao.create_assignment(
            skill_id,
            auth.tenant_id,
            target_type=body.target_type,
            target_id=body.target_id,
            enabled=body.enabled,
            priority_override=body.priority_override,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SkillAssignmentResponse.model_validate(assignment)


@router.delete("/{skill_id}/assign/{assignment_id}", status_code=204)
async def delete_assignment(
    skill_id: uuid.UUID,
    assignment_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a skill assignment."""
    dao = SkillDao(session)
    deleted = await dao.delete_assignment(assignment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Assignment not found.")


# ── Import / Export ───────────────────────────────────────────────────


@router.post("/import", response_model=SkillDetailResponse, status_code=201)
async def import_skill(
    file: UploadFile,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Import a skill from a .md file upload."""
    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(
            status_code=422,
            detail="Only .md files are supported.",
        )
    content = (await file.read()).decode("utf-8")
    parsed = parse_skill_document(content)
    metadata = extract_metadata_from_frontmatter(parsed.frontmatter)

    name = metadata.pop("name", None) or file.filename.removesuffix(".md")

    try:
        skill = await SkillDao(session).create_skill(
            auth.tenant_id,
            name=name,
            body_markdown=parsed.body,
            frontmatter_yaml=parsed.raw_frontmatter or None,
            created_by=auth.user_id,
            **metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _skill_to_detail(skill)


@router.get("/{skill_id}/export")
async def export_skill(
    skill_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> PlainTextResponse:
    """Export a skill as a .md file."""
    dao = SkillDao(session)
    skill = await dao.get_skill(skill_id, auth.tenant_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found.")

    version = await dao.get_current_version_body(skill_id)
    body = version.body_markdown if version else ""

    frontmatter: dict[str, Any] = {
        "name": skill.name,
        "description": skill.description,
        "scope": skill.scope,
        "priority": skill.priority,
        "tags": skill.tags or [],
    }
    if skill.allowed_tools:
        frontmatter["allowed_tools"] = skill.allowed_tools
    if skill.preferred_tools:
        frontmatter["preferred_tools"] = skill.preferred_tools
    if skill.forbidden_tools:
        frontmatter["forbidden_tools"] = skill.forbidden_tools
    if skill.knowledge_sources:
        frontmatter["knowledge_sources"] = skill.knowledge_sources
    if skill.trigger_rules:
        frontmatter["trigger_rules"] = skill.trigger_rules

    content = compose_skill_document(frontmatter, body)
    return PlainTextResponse(
        content=content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{skill.slug}.md"',
        },
    )


# ── Completions (Intellisense) ────────────────────────────────────────


@router.get("/completions", response_model=CompletionsResponse)
async def get_completions(
    auth: AuthContext = Depends(get_auth_context),
) -> CompletionsResponse:
    """Return intellisense completion data for the skill editor."""
    return CompletionsResponse(
        frontmatter_keys=[
            {"key": "name", "description": "Skill display name"},
            {"key": "description", "description": "Short description"},
            {"key": "scope", "description": "Visibility scope"},
            {"key": "status", "description": "Lifecycle status"},
            {"key": "enabled", "description": "Whether the skill is active"},
            {"key": "priority", "description": "Resolution priority (0-100)"},
            {"key": "tags", "description": "Categorization tags"},
            {"key": "allowed_tools", "description": "MCP tools this skill may use"},
            {"key": "preferred_tools", "description": "Tools to prioritize"},
            {"key": "forbidden_tools", "description": "Tools to exclude"},
            {"key": "knowledge_sources", "description": "RAG collection IDs"},
            {"key": "trigger_rules", "description": "Auto-activation rules"},
        ],
        scope_values=["global", "tenant", "workspace", "assistant", "user"],
        status_values=["draft", "published", "archived"],
        section_templates=[
            {"name": "## Goal", "description": "Define the skill's objective"},
            {"name": "## Procedure", "description": "Step-by-step instructions"},
            {
                "name": "## Output format",
                "description": "Expected output structure",
            },
            {"name": "## Do not", "description": "Explicit constraints"},
            {"name": "## Escalation", "description": "When to escalate or stop"},
            {"name": "## Examples", "description": "Few-shot examples"},
        ],
        trigger_intents=[
            "financial_analysis",
            "code_review",
            "summarization",
            "translation",
            "data_extraction",
            "writing",
            "research",
            "debugging",
        ],
        file_types=[
            "pdf",
            "xlsx",
            "docx",
            "csv",
            "json",
            "md",
            "txt",
            "html",
            "pptx",
        ],
    )


# ── Helpers ───────────────────────────────────────────────────────────


def _skill_to_detail(skill: Any) -> SkillDetailResponse:
    """Convert a SkillModel (with loaded relations) to detail response."""
    body_md = None
    fm_yaml = None
    if skill.versions:
        current = next(
            (v for v in skill.versions if v.version == skill.current_version),
            skill.versions[0] if skill.versions else None,
        )
        if current:
            body_md = current.body_markdown
            fm_yaml = current.frontmatter_yaml

    return SkillDetailResponse(
        id=skill.id,
        tenant_id=skill.tenant_id,
        name=skill.name,
        slug=skill.slug,
        description=skill.description,
        scope=skill.scope,
        status=skill.status,
        enabled=skill.enabled,
        priority=skill.priority,
        tags=skill.tags,
        current_version=skill.current_version,
        created_by=skill.created_by,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
        allowed_tools=skill.allowed_tools,
        preferred_tools=skill.preferred_tools,
        forbidden_tools=skill.forbidden_tools,
        knowledge_sources=skill.knowledge_sources,
        trigger_rules=skill.trigger_rules,
        body_markdown=body_md,
        frontmatter_yaml=fm_yaml,
        assignments=[
            SkillAssignmentResponse.model_validate(a)
            for a in (skill.assignments or [])
        ],
    )
