"""Inference API route module — aggregates control-plane, environment, and deployment routes."""

from fastapi import APIRouter

from llm_port_backend.web.api.inference.control_planes import (
    router as control_planes_router,
)
from llm_port_backend.web.api.inference.deployments import router as deployments_router
from llm_port_backend.web.api.inference.environments import (
    router as environments_router,
)

inference_router = APIRouter()
inference_router.include_router(
    control_planes_router,
    prefix="/control-planes",
    tags=["inference-control-planes"],
)
inference_router.include_router(
    environments_router,
    prefix="/environments",
    tags=["inference-environments"],
)
inference_router.include_router(
    deployments_router,
    prefix="/deployments",
    tags=["inference-deployments"],
)
