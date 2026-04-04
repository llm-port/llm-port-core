"""Data Access Object for Skills CRUD operations."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_skills.db.models.skill import (
    SkillAssignmentModel,
    SkillModel,
    SkillStatus,
    SkillVersionModel,
)
from llm_port_skills.settings import settings


class SkillDao:
    """Encapsulates all DB operations for skills."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _slugify(name: str) -> str:
        slug = name.lower().strip()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        return slug.strip("-")[:255]

    # ── Skill CRUD ────────────────────────────────────────────────────

    async def list_skills(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        scope: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[SkillModel], int]:
        """List skills for a tenant with optional filters."""
        q = select(SkillModel).where(SkillModel.tenant_id == tenant_id)
        count_q = (
            select(func.count())
            .select_from(SkillModel)
            .where(SkillModel.tenant_id == tenant_id)
        )

        if status:
            q = q.where(SkillModel.status == status)
            count_q = count_q.where(SkillModel.status == status)
        if scope:
            q = q.where(SkillModel.scope == scope)
            count_q = count_q.where(SkillModel.scope == scope)
        if tag:
            q = q.where(SkillModel.tags.contains([tag]))
            count_q = count_q.where(SkillModel.tags.contains([tag]))
        if search:
            pattern = f"%{search}%"
            q = q.where(
                SkillModel.name.ilike(pattern)
                | SkillModel.description.ilike(pattern),
            )
            count_q = count_q.where(
                SkillModel.name.ilike(pattern)
                | SkillModel.description.ilike(pattern),
            )

        q = q.order_by(SkillModel.updated_at.desc())
        q = q.offset(offset).limit(limit)

        total = (await self._session.execute(count_q)).scalar() or 0
        result = await self._session.execute(q)
        return list(result.scalars().all()), total

    async def get_skill(
        self,
        skill_id: uuid.UUID,
        tenant_id: str,
    ) -> SkillModel | None:
        """Get a single skill by ID."""
        q = select(SkillModel).where(
            SkillModel.id == skill_id,
            SkillModel.tenant_id == tenant_id,
        )
        result = await self._session.execute(q)
        return result.scalar_one_or_none()

    async def create_skill(
        self,
        tenant_id: str,
        *,
        name: str,
        body_markdown: str,
        frontmatter_yaml: str | None = None,
        created_by: str | None = None,
        **kwargs: Any,
    ) -> SkillModel:
        """Create a new skill with its initial version."""
        # Check published limit
        count_q = (
            select(func.count())
            .select_from(SkillModel)
            .where(
                SkillModel.tenant_id == tenant_id,
                SkillModel.status == SkillStatus.PUBLISHED.value,
            )
        )
        published_count = (await self._session.execute(count_q)).scalar() or 0
        if published_count >= settings.max_published_skills_per_tenant:
            msg = (
                f"Tenant has reached the maximum of "
                f"{settings.max_published_skills_per_tenant} published skills."
            )
            raise ValueError(msg)

        slug = kwargs.pop("slug", None) or self._slugify(name)
        skill = SkillModel(
            tenant_id=tenant_id,
            name=name,
            slug=slug,
            created_by=created_by,
            current_version=1,
            **kwargs,
        )
        self._session.add(skill)
        await self._session.flush()

        version = SkillVersionModel(
            skill_id=skill.id,
            version=1,
            body_markdown=body_markdown,
            frontmatter_yaml=frontmatter_yaml,
            change_note="Initial version",
            created_by=created_by,
        )
        self._session.add(version)
        await self._session.flush()
        # Explicitly load relationships so callers can access them
        # without triggering a sync lazy-load (MissingGreenlet).
        await self._session.refresh(skill, ["versions", "assignments"])
        return skill

    async def update_skill_metadata(
        self,
        skill_id: uuid.UUID,
        tenant_id: str,
        **kwargs: Any,
    ) -> SkillModel | None:
        """Update skill metadata fields (not the body)."""
        skill = await self.get_skill(skill_id, tenant_id)
        if skill is None:
            return None
        for key, value in kwargs.items():
            if hasattr(skill, key) and key not in ("id", "tenant_id", "created_at"):
                setattr(skill, key, value)
        await self._session.flush()
        return skill

    async def update_skill_body(
        self,
        skill_id: uuid.UUID,
        tenant_id: str,
        *,
        body_markdown: str,
        frontmatter_yaml: str | None = None,
        change_note: str | None = None,
        created_by: str | None = None,
    ) -> SkillVersionModel | None:
        """Create a new version with updated body content."""
        skill = await self.get_skill(skill_id, tenant_id)
        if skill is None:
            return None

        # Enforce version limit — remove oldest beyond limit
        versions_q = (
            select(SkillVersionModel)
            .where(SkillVersionModel.skill_id == skill_id)
            .order_by(SkillVersionModel.version.desc())
        )
        versions = list(
            (await self._session.execute(versions_q)).scalars().all(),
        )
        if len(versions) >= settings.max_versions_per_skill:
            for old in versions[settings.max_versions_per_skill - 1 :]:
                await self._session.delete(old)

        new_version_num = skill.current_version + 1
        version = SkillVersionModel(
            skill_id=skill_id,
            version=new_version_num,
            body_markdown=body_markdown,
            frontmatter_yaml=frontmatter_yaml,
            change_note=change_note,
            created_by=created_by,
        )
        self._session.add(version)
        skill.current_version = new_version_num
        await self._session.flush()
        return version

    async def delete_skill(
        self,
        skill_id: uuid.UUID,
        tenant_id: str,
    ) -> bool:
        """Delete a skill and all its versions/assignments."""
        skill = await self.get_skill(skill_id, tenant_id)
        if skill is None:
            return False
        await self._session.delete(skill)
        await self._session.flush()
        return True

    async def set_status(
        self,
        skill_id: uuid.UUID,
        tenant_id: str,
        status: str,
    ) -> SkillModel | None:
        """Update a skill's status (draft/published/archived)."""
        if status == SkillStatus.PUBLISHED.value:
            count_q = (
                select(func.count())
                .select_from(SkillModel)
                .where(
                    SkillModel.tenant_id == tenant_id,
                    SkillModel.status == SkillStatus.PUBLISHED.value,
                    SkillModel.id != skill_id,
                )
            )
            count = (await self._session.execute(count_q)).scalar() or 0
            if count >= settings.max_published_skills_per_tenant:
                msg = (
                    f"Tenant has reached the maximum of "
                    f"{settings.max_published_skills_per_tenant} published skills."
                )
                raise ValueError(msg)

        return await self.update_skill_metadata(
            skill_id,
            tenant_id,
            status=status,
        )

    # ── Version queries ───────────────────────────────────────────────

    async def list_versions(
        self,
        skill_id: uuid.UUID,
    ) -> list[SkillVersionModel]:
        """List all versions for a skill."""
        q = (
            select(SkillVersionModel)
            .where(SkillVersionModel.skill_id == skill_id)
            .order_by(SkillVersionModel.version.desc())
        )
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def get_version(
        self,
        skill_id: uuid.UUID,
        version: int,
    ) -> SkillVersionModel | None:
        """Get a specific version of a skill."""
        q = select(SkillVersionModel).where(
            SkillVersionModel.skill_id == skill_id,
            SkillVersionModel.version == version,
        )
        result = await self._session.execute(q)
        return result.scalar_one_or_none()

    async def get_current_version_body(
        self,
        skill_id: uuid.UUID,
    ) -> SkillVersionModel | None:
        """Get the current (latest) version of a skill."""
        skill_q = select(SkillModel.current_version).where(
            SkillModel.id == skill_id,
        )
        current_ver = (await self._session.execute(skill_q)).scalar()
        if current_ver is None:
            return None
        return await self.get_version(skill_id, current_ver)

    # ── Assignments ───────────────────────────────────────────────────

    async def list_assignments(
        self,
        skill_id: uuid.UUID,
    ) -> list[SkillAssignmentModel]:
        """List all assignments for a skill."""
        q = select(SkillAssignmentModel).where(
            SkillAssignmentModel.skill_id == skill_id,
        )
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def create_assignment(
        self,
        skill_id: uuid.UUID,
        tenant_id: str,
        *,
        target_type: str,
        target_id: str | None = None,
        enabled: bool = True,
        priority_override: int | None = None,
    ) -> SkillAssignmentModel:
        """Create a new skill assignment."""
        # Check assignment limit
        count_q = (
            select(func.count())
            .select_from(SkillAssignmentModel)
            .join(SkillModel, SkillAssignmentModel.skill_id == SkillModel.id)
            .where(SkillModel.tenant_id == tenant_id)
        )
        count = (await self._session.execute(count_q)).scalar() or 0
        if count >= settings.max_assignments_per_tenant:
            msg = (
                f"Tenant has reached the maximum of "
                f"{settings.max_assignments_per_tenant} assignments."
            )
            raise ValueError(msg)

        assignment = SkillAssignmentModel(
            skill_id=skill_id,
            target_type=target_type,
            target_id=target_id,
            enabled=enabled,
            priority_override=priority_override,
        )
        self._session.add(assignment)
        await self._session.flush()
        return assignment

    async def delete_assignment(
        self,
        assignment_id: uuid.UUID,
    ) -> bool:
        """Delete an assignment."""
        q = select(SkillAssignmentModel).where(
            SkillAssignmentModel.id == assignment_id,
        )
        result = await self._session.execute(q)
        assignment = result.scalar_one_or_none()
        if assignment is None:
            return False
        await self._session.delete(assignment)
        await self._session.flush()
        return True

    # ── Resolution queries ────────────────────────────────────────────

    async def resolve_skills(
        self,
        tenant_id: str,
        *,
        assistant_id: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
        user_query: str | None = None,
        file_types: list[str] | None = None,
        tags: list[str] | None = None,
        max_results: int = 3,
    ) -> list[tuple[SkillModel, SkillVersionModel, int]]:
        """Resolve matching skills for a request context.

        Returns list of (skill, current_version, score) tuples
        sorted by score descending.
        """
        # Get all published, enabled skills for tenant
        q = select(SkillModel).where(
            SkillModel.tenant_id == tenant_id,
            SkillModel.status == SkillStatus.PUBLISHED.value,
            SkillModel.enabled.is_(True),
        )
        result = await self._session.execute(q)
        candidates = list(result.scalars().all())

        scored: list[tuple[SkillModel, int]] = []
        for skill in candidates:
            score = skill.priority

            # Assignment matching
            for assignment in skill.assignments:
                if not assignment.enabled:
                    continue
                matched = False
                if (
                    assignment.target_type == "assistant"
                    and assignment.target_id == assistant_id
                ):
                    matched = True
                elif (
                    assignment.target_type == "workspace"
                    and assignment.target_id == workspace_id
                ):
                    matched = True
                elif (
                    assignment.target_type == "project"
                    and assignment.target_id == project_id
                ):
                    matched = True
                elif assignment.target_type == "global":
                    matched = True
                elif assignment.target_type == "tenant":
                    matched = True

                if matched:
                    score += 50
                    if assignment.priority_override is not None:
                        score = assignment.priority_override

            # Trigger matching
            triggers = skill.trigger_rules or {}
            if user_query:
                keywords = triggers.get("keywords", [])
                for kw in keywords:
                    if kw.lower() in user_query.lower():
                        score += 30

            if file_types:
                trigger_file_types = triggers.get("file_types", [])
                for ft in trigger_file_types:
                    if ft in file_types:
                        score += 30

            if tags:
                skill_tags = skill.tags or []
                for t in tags:
                    if t in skill_tags:
                        score += 20

            scored.append((skill, score))

        # Sort by score descending, take top N
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:max_results]

        # Fetch current version bodies
        results: list[tuple[SkillModel, SkillVersionModel, int]] = []
        for skill, score in top:
            if score <= skill.priority:
                # No trigger/assignment match — skip unless it's
                # a global/tenant scope with base priority
                if skill.scope not in ("global", "tenant"):
                    continue
            version = await self.get_current_version_body(skill.id)
            if version:
                results.append((skill, version, score))

        return results
