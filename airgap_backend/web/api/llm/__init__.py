"""LLM API route module — aggregates provider, model, runtime, and job routes."""

from fastapi import APIRouter

from airgap_backend.web.api.llm.jobs import router as jobs_router
from airgap_backend.web.api.llm.models import router as models_router
from airgap_backend.web.api.llm.providers import router as providers_router
from airgap_backend.web.api.llm.runtimes import router as runtimes_router
from airgap_backend.web.api.llm.search import router as search_router
from airgap_backend.web.api.llm.settings_routes import router as settings_router

llm_router = APIRouter()
llm_router.include_router(providers_router, prefix="/providers", tags=["llm-providers"])
llm_router.include_router(models_router, prefix="/models", tags=["llm-models"])
llm_router.include_router(runtimes_router, prefix="/runtimes", tags=["llm-runtimes"])
llm_router.include_router(jobs_router, prefix="/jobs", tags=["llm-jobs"])
llm_router.include_router(search_router, prefix="/search", tags=["llm-search"])
llm_router.include_router(settings_router, prefix="/settings", tags=["llm-settings"])
