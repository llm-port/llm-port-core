from fastapi.routing import APIRouter

from llm_port_api.web.api import docs, echo, monitoring, rabbit, redis
from llm_port_api.web.api.v1 import router as v1_router

api_router = APIRouter()
public_router = APIRouter()
api_router.include_router(monitoring.router)
api_router.include_router(docs.router)
api_router.include_router(echo.router, prefix="/echo", tags=["echo"])
api_router.include_router(redis.router, prefix="/redis", tags=["redis"])
api_router.include_router(rabbit.router, prefix="/rabbit", tags=["rabbit"])
public_router.include_router(v1_router, tags=["gateway"])
