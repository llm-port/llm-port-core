"""Pydantic schemas for the stacks (compose) API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StackRevisionDTO(BaseModel):
    """A single revision of a compose stack."""

    id: uuid.UUID
    stack_id: str
    rev: int
    compose_yaml: str
    env_blob: str | None = None
    image_digests: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StackSummaryDTO(BaseModel):
    """Summary of a deployed stack (latest revision)."""

    stack_id: str
    latest_rev: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeployStackRequest(BaseModel):
    """Payload for deploying or updating a compose stack."""

    stack_id: str = Field(..., description="Unique identifier for this stack.")
    compose_yaml: str = Field(..., description="Full docker-compose YAML content.")
    env_blob: str | None = Field(None, description="JSON-encoded env var overrides.")
    image_digests: str | None = Field(None, description="JSON-encoded image digest map.")


class RollbackStackRequest(BaseModel):
    """Request to roll back a stack to a specific revision."""

    rev: int = Field(..., description="Revision number to roll back to.")


class StackDiffDTO(BaseModel):
    """Diff between two stack revisions."""

    stack_id: str
    from_rev: int
    to_rev: int
    compose_yaml_from: str
    compose_yaml_to: str
    env_blob_from: str | None = None
    env_blob_to: str | None = None
    image_digests_from: str | None = None
    image_digests_to: str | None = None
