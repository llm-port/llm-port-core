"""SQLAlchemy ORM models for the Skills Registry."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for Skills models."""


# ── Enums ─────────────────────────────────────────────────────────────


class SkillScope(str, enum.Enum):
    """Scope levels for skill visibility."""

    GLOBAL = "global"
    TENANT = "tenant"
    WORKSPACE = "workspace"
    ASSISTANT = "assistant"
    USER = "user"


class SkillStatus(str, enum.Enum):
    """Skill lifecycle states."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AssignmentTargetType(str, enum.Enum):
    """Target types for skill assignments."""

    ASSISTANT = "assistant"
    WORKSPACE = "workspace"
    PROJECT = "project"
    TENANT = "tenant"
    GLOBAL = "global"


# ── Models ────────────────────────────────────────────────────────────


class SkillModel(Base):
    """A reusable instruction pack (reasoning playbook)."""

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_skill_tenant_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="default",
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scope: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SkillScope.TENANT.value,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SkillStatus.DRAFT.value,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    allowed_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    preferred_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    forbidden_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    knowledge_sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    trigger_rules: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    current_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    versions: Mapped[list[SkillVersionModel]] = relationship(
        back_populates="skill",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SkillVersionModel.version.desc()",
    )
    assignments: Mapped[list[SkillAssignmentModel]] = relationship(
        back_populates="skill",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class SkillVersionModel(Base):
    """An immutable snapshot of a skill's body content."""

    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint(
            "skill_id",
            "version",
            name="uq_skill_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    frontmatter_yaml: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    skill: Mapped[SkillModel] = relationship(back_populates="versions")


class SkillAssignmentModel(Base):
    """Binds a skill to a specific target (assistant, workspace, etc.)."""

    __tablename__ = "skill_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    skill: Mapped[SkillModel] = relationship(back_populates="assignments")
