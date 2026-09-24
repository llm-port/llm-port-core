"""LLM Settings endpoints - HF token management.

The token is stored as an encrypted secret in the system_settings DB table
(key ``llm_backend.hf_token``).  It is **never** sent over RabbitMQ or
exposed in API responses: callers see whether one is set, where it comes
from, and whom Hugging Face says it belongs to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.users import User
from llm_port_backend.services.llm import hf_token
from llm_port_backend.web.api.llm.schema import HFTokenSetRequest, HFTokenStatusDTO
from llm_port_backend.web.api.rbac import require_permission

HF_TOKEN_KEY = hf_token.HF_TOKEN_KEY

router = APIRouter()


@router.get("/hf-token", response_model=HFTokenStatusDTO)
async def get_hf_token_status(
    user: User = Depends(require_permission("llm.settings", "read")),
    session: AsyncSession = Depends(get_db_session),
) -> HFTokenStatusDTO:
    """Whether a Hugging Face token is set and whom it belongs to (never the token)."""
    return HFTokenStatusDTO(**await hf_token.status(session))


@router.put("/hf-token", response_model=HFTokenStatusDTO)
async def set_hf_token(
    body: HFTokenSetRequest,
    user: User = Depends(require_permission("llm.settings", "update")),
    session: AsyncSession = Depends(get_db_session),
) -> HFTokenStatusDTO:
    """Check the token with Hugging Face, then keep it encrypted."""
    try:
        return HFTokenStatusDTO(**await hf_token.store(session, body.token.get_secret_value(), user.id))
    except hf_token.TokenRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    except hf_token.TokenStoreUnsafe as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None


@router.delete("/hf-token", response_model=HFTokenStatusDTO)
async def remove_hf_token(
    user: User = Depends(require_permission("llm.settings", "update")),
    session: AsyncSession = Depends(get_db_session),
) -> HFTokenStatusDTO:
    """Forget the stored token."""
    return HFTokenStatusDTO(**await hf_token.remove(session, user.id))
