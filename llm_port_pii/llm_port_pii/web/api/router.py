from fastapi.routing import APIRouter

from llm_port_pii.web.api import docs, monitoring
from llm_port_pii.web.api.pii.views import router as pii_router

api_router = APIRouter()
api_router.include_router(monitoring.router)
api_router.include_router(docs.router)
api_router.include_router(pii_router, prefix="/v1/pii", tags=["pii"])
