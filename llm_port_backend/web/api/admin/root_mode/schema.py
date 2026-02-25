"""Pydantic schemas for the root mode (break-glass) API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StartRootModeRequest(BaseModel):
    """Payload for activating a root mode session."""

    reason: str = Field(..., min_length=10, description="Mandatory reason for root access.")
    scope: str = Field(default="all", description="Target scope (engine ID or 'all').")
    duration_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        description="Session lifetime in seconds (60-3600).",
    )


class RootSessionDTO(BaseModel):
    """Representation of a root mode session."""

    id: uuid.UUID
    actor_id: uuid.UUID
    start_time: datetime
    end_time: datetime | None = None
    reason: str
    scope: str
    duration_seconds: int
    active: bool = False

    model_config = ConfigDict(from_attributes=True)


class RootModeStatusDTO(BaseModel):
    """Whether the current user has an active root mode session."""

    active: bool
    session: RootSessionDTO | None = None
