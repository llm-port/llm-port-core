"""HF model search endpoint — proxies huggingface_hub list_models for autocomplete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from pydantic import BaseModel

from llm_port_backend.db.models.users import User
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()


class HFModelHit(BaseModel):
    """Minimal model info returned by search."""

    id: str
    downloads: int = 0
    likes: int = 0
    pipeline_tag: str | None = None


@router.get("/hf-search", response_model=list[HFModelHit])
async def search_hf_models(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    _user: User = Depends(require_permission("llm.models", "read")),
) -> list[HFModelHit]:
    """
    Search Hugging Face Hub for models matching *q*.

    Uses the ``huggingface_hub`` Python client (already a project dependency).
    In a fully air-gapped deployment without internet access this endpoint
    will return an empty list rather than raising.
    """
    try:
        from huggingface_hub import HfApi  # noqa: PLC0415

        api = HfApi()
        hits = list(api.list_models(search=q, limit=limit, sort="downloads", direction=-1))
        return [
            HFModelHit(
                id=m.modelId,
                downloads=getattr(m, "downloads", 0) or 0,
                likes=getattr(m, "likes", 0) or 0,
                pipeline_tag=getattr(m, "pipeline_tag", None),
            )
            for m in hits
        ]
    except Exception:  # noqa: BLE001
        return []
