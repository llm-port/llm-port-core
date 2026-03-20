"""Pydantic schemas for Skills admin API requests and responses."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Request schemas ───────────────────────────────────────────────────


class CreateSkillRequest(BaseModel):
    """Create a new skill from frontmatter+markdown document."""

    content: str = Field(
        min_length=1,
        description="Full skill document (YAML frontmatter + markdown body)",
    )
    name: str | None = Field(
        default=None,
        max_length=255,
        description="Override name (otherwise extracted from frontmatter)",
    )


class UpdateSkillMetadataRequest(BaseModel):
    """Update editable skill metadata fields."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    scope: str | None = Field(
        default=None,
        pattern=r"^(global|tenant|workspace|assistant|user)$",
    )
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    tags: list[str] | None = None
    allowed_tools: list[str] | None = None
    preferred_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    knowledge_sources: list[str] | None = None
    trigger_rules: dict[str, Any] | None = None


class UpdateSkillBodyRequest(BaseModel):
    """Update the skill body (creates a new version)."""

    content: str = Field(
        min_length=1,
        description="Full skill document (YAML frontmatter + markdown body)",
    )
    change_note: str | None = Field(default=None, max_length=500)


class CreateAssignmentRequest(BaseModel):
    """Create a skill assignment."""

    target_type: str = Field(
        pattern=r"^(assistant|workspace|project|tenant|global)$",
    )
    target_id: str | None = None
    enabled: bool = True
    priority_override: int | None = Field(default=None, ge=0, le=100)


# ── Response schemas ──────────────────────────────────────────────────


class SkillVersionResponse(BaseModel):
    """A skill version snapshot."""

    id: uuid.UUID
    skill_id: uuid.UUID
    version: int
    body_markdown: str
    frontmatter_yaml: str | None
    change_note: str | None
    created_by: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillAssignmentResponse(BaseModel):
    """A skill assignment."""

    id: uuid.UUID
    skill_id: uuid.UUID
    target_type: str
    target_id: str | None
    enabled: bool
    priority_override: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillSummaryResponse(BaseModel):
    """Skill summary for list views."""

    id: uuid.UUID
    tenant_id: str
    name: str
    slug: str
    description: str
    scope: str
    status: str
    enabled: bool
    priority: int
    tags: list[str] | None
    current_version: int
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillDetailResponse(SkillSummaryResponse):
    """Full skill detail with current version body."""

    allowed_tools: list[str] | None
    preferred_tools: list[str] | None
    forbidden_tools: list[str] | None
    knowledge_sources: list[str] | None
    trigger_rules: dict[str, Any] | None
    body_markdown: str | None = None
    frontmatter_yaml: str | None = None
    assignments: list[SkillAssignmentResponse] = []


class SkillListResponse(BaseModel):
    """Paginated skill list."""

    items: list[SkillSummaryResponse]
    total: int
    offset: int
    limit: int


class CompletionsResponse(BaseModel):
    """Intellisense completion data for the skill editor."""

    frontmatter_keys: list[dict[str, str]]
    scope_values: list[str]
    status_values: list[str]
    section_templates: list[dict[str, str]]
    trigger_intents: list[str]
    file_types: list[str]
