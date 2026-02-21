"""Pydantic schemas for images API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImageSummaryDTO(BaseModel):
    """Summary of a local Docker image."""

    id: str
    repo_tags: list[str] = Field(default_factory=list)
    repo_digests: list[str] = Field(default_factory=list)
    size: int = 0
    created: str = ""


class PullImageRequest(BaseModel):
    """Request body for pulling an image."""

    image: str = Field(..., description="Image name, e.g. nginx")
    tag: str = Field(default="latest", description="Image tag")


class PruneImagesRequest(BaseModel):
    """Optional request body to constrain prune scope."""

    dry_run: bool = Field(default=False, description="If True, returns what would be pruned.")


class PruneReport(BaseModel):
    """Report from an image prune operation."""

    deleted: list[str] = Field(default_factory=list)
    space_reclaimed: int = 0
    dry_run: bool = False
