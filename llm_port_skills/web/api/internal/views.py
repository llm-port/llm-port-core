"""Internal API views for gateway-facing skill resolution and telemetry."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_skills.db.session import get_db_session
from llm_port_skills.services.dao import SkillDao
from llm_port_skills.web.api.auth import verify_service_token

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_service_token)])


# ── Schemas ───────────────────────────────────────────────────────────


class SkillResolveRequest(BaseModel):
    """Request to resolve matching skills for a gateway request."""

    tenant_id: str
    user_id: str | None = None
    assistant_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    user_query: str | None = None
    file_types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    max_skills: int = Field(default=3, ge=1, le=10)


class ResolvedSkill(BaseModel):
    """A resolved skill ready for injection."""

    skill_id: str
    name: str
    slug: str
    version: int
    body_markdown: str
    priority: int
    score: int
    allowed_tools: list[str] | None = None
    preferred_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    knowledge_sources: list[str] | None = None


class SkillResolveResponse(BaseModel):
    """Response containing resolved skills."""

    skills: list[ResolvedSkill]


class SkillUsageRequest(BaseModel):
    """Record skill usage telemetry."""

    tenant_id: str
    skill_id: str
    version: int
    session_id: str | None = None
    user_id: str | None = None
    matched_by: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/skills/resolve", response_model=SkillResolveResponse)
async def resolve_skills(
    body: SkillResolveRequest,
    session: AsyncSession = Depends(get_db_session),
) -> SkillResolveResponse:
    """Resolve matching skills for a gateway request context."""
    dao = SkillDao(session)
    results = await dao.resolve_skills(
        body.tenant_id,
        assistant_id=body.assistant_id,
        workspace_id=body.workspace_id,
        project_id=body.project_id,
        user_query=body.user_query,
        file_types=body.file_types or None,
        tags=body.tags or None,
        max_results=body.max_skills,
    )

    skills = []
    for skill, version, score in results:
        skills.append(
            ResolvedSkill(
                skill_id=str(skill.id),
                name=skill.name,
                slug=skill.slug,
                version=version.version,
                body_markdown=version.body_markdown,
                priority=skill.priority,
                score=score,
                allowed_tools=skill.allowed_tools,
                preferred_tools=skill.preferred_tools,
                forbidden_tools=skill.forbidden_tools,
                knowledge_sources=skill.knowledge_sources,
            ),
        )
    return SkillResolveResponse(skills=skills)


@router.post("/skills/usage", status_code=204)
async def record_usage(
    body: SkillUsageRequest,
) -> None:
    """Record skill usage telemetry (fire-and-forget)."""
    logger.info(
        "Skill usage: tenant=%s skill=%s v%d session=%s user=%s match=%s",
        body.tenant_id,
        body.skill_id,
        body.version,
        body.session_id,
        body.user_id,
        body.matched_by,
    )
