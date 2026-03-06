from fastapi.routing import APIRouter

from llm_port_api.web.api import docs, monitoring
from llm_port_api.web.api.v1 import router as v1_router

api_router = APIRouter()
public_router = APIRouter()
api_router.include_router(monitoring.router)
api_router.include_router(docs.router)
public_router.include_router(v1_router, tags=["gateway"])
