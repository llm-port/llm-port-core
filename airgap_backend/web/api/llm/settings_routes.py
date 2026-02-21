"""LLM Settings endpoints — HF token management."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from airgap_backend.db.models.users import User
from airgap_backend.settings import settings
from airgap_backend.web.api.llm.schema import HFTokenSetRequest, HFTokenStatusDTO
from airgap_backend.web.api.rbac import require_permission

router = APIRouter()


@router.get("/hf-token", response_model=HFTokenStatusDTO)
async def get_hf_token_status(
    user: User = Depends(require_permission("llm.settings", "read")),
) -> HFTokenStatusDTO:
    """Check whether a Hugging Face token is configured (never returns the token)."""
    return HFTokenStatusDTO(configured=bool(settings.hf_token))


@router.put("/hf-token", response_model=HFTokenStatusDTO)
async def set_hf_token(
    body: HFTokenSetRequest,
    user: User = Depends(require_permission("llm.settings", "update")),
) -> HFTokenStatusDTO:
    """
    Set the Hugging Face token.

    MVP: updates the in-memory settings object. For persistence across
    restarts, also set the ``AIRGAP_BACKEND_HF_TOKEN`` env var or store
    in the database (future enhancement).
    """
    settings.hf_token = body.token
    return HFTokenStatusDTO(configured=True)
