"""Shared FastAPI dependencies for the /llm namespace."""

from __future__ import annotations

from fastapi import Depends, Request

from llm_port_backend.db.dao.llm_dao import ModelDAO, ProviderDAO, RuntimeDAO
from llm_port_backend.services.llm.graph_service import LLMGraphService
from llm_port_backend.services.llm.service import LLMService


def get_llm_service(request: Request) -> LLMService:
    """Retrieve the shared LLMService from app state."""
    return request.app.state.llm_service  # type: ignore[no-any-return]


def get_llm_graph_service(
    request: Request,
    provider_dao: ProviderDAO = Depends(),
    runtime_dao: RuntimeDAO = Depends(),
    model_dao: ModelDAO = Depends(),
) -> LLMGraphService:
    """Build a graph service with DAO dependencies and optional trace DB access."""
    return LLMGraphService(
        provider_dao=provider_dao,
        runtime_dao=runtime_dao,
        model_dao=model_dao,
        trace_session_factory=getattr(request.app.state, "llm_graph_trace_session_factory", None),
    )
