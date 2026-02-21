"""Shared FastAPI dependencies for the /llm namespace."""

from __future__ import annotations

from fastapi import Request

from airgap_backend.services.llm.service import LLMService


def get_llm_service(request: Request) -> LLMService:
    """Retrieve the shared LLMService from app state."""
    return request.app.state.llm_service  # type: ignore[no-any-return]
