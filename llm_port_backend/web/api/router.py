from fastapi.routing import APIRouter

from llm_port_backend.web.api import docs, i18n, logs, monitoring, users
from llm_port_backend.web.api.admin import admin_router
from llm_port_backend.web.api.bootstrap.views import router as bootstrap_router
from llm_port_backend.web.api.chat.views import router as chat_router
from llm_port_backend.web.api.llm import llm_router
from llm_port_backend.web.api.node_files import router as node_files_router

api_router = APIRouter()
api_router.include_router(monitoring.router)
api_router.include_router(users.router)
api_router.include_router(docs.router)
api_router.include_router(bootstrap_router)
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
api_router.include_router(llm_router, prefix="/llm", tags=["llm"])
api_router.include_router(chat_router, tags=["chat"])
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
api_router.include_router(i18n.router, prefix="/i18n", tags=["i18n"])
api_router.include_router(node_files_router)
