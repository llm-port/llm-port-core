from fastapi import APIRouter
from fastapi.responses import UJSONResponse

from llm_port_mcp.web.api.admin.views import router as admin_router
from llm_port_mcp.web.api.internal.views import router as internal_router

api_router = APIRouter()


@api_router.get("/health", tags=["monitoring"])
async def health_check() -> UJSONResponse:
    """Public health endpoint."""
    return UJSONResponse({"status": "ok"})


api_router.include_router(admin_router, prefix="/v1/mcp", tags=["MCP Admin"])
api_router.include_router(
    internal_router,
    prefix="/internal",
    tags=["MCP Internal"],
)
