"""Admin API router — aggregates all /admin subroutes."""

import logging

from fastapi import APIRouter

from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.audit.views import router as audit_router
from llm_port_backend.web.api.admin.containers.views import router as containers_router
from llm_port_backend.web.api.admin.dashboard.views import router as dashboard_router
from llm_port_backend.web.api.admin.hardware.views import router as hardware_router
from llm_port_backend.web.api.admin.images.views import router as images_router
from llm_port_backend.web.api.admin.networks.views import router as networks_router
from llm_port_backend.web.api.admin.root_mode.views import router as root_mode_router
from llm_port_backend.web.api.admin.services.views import router as services_router
from llm_port_backend.web.api.admin.stacks.views import router as stacks_router
from llm_port_backend.web.api.admin.system.views import router as system_router
from llm_port_backend.web.api.admin.users.views import router as users_router

logger = logging.getLogger(__name__)

admin_router = APIRouter()
admin_router.include_router(containers_router, prefix="/containers", tags=["admin-containers"])
admin_router.include_router(dashboard_router, prefix="/dashboard", tags=["admin-dashboard"])
admin_router.include_router(hardware_router, prefix="/hardware", tags=["admin-hardware"])
admin_router.include_router(images_router, prefix="/images", tags=["admin-images"])
admin_router.include_router(stacks_router, prefix="/stacks", tags=["admin-stacks"])
admin_router.include_router(networks_router, prefix="/networks", tags=["admin-networks"])
admin_router.include_router(root_mode_router, prefix="/root-mode", tags=["admin-root-mode"])
admin_router.include_router(audit_router, prefix="/audit", tags=["admin-audit"])
admin_router.include_router(users_router, prefix="/users", tags=["admin-users"])
admin_router.include_router(system_router, prefix="/system", tags=["admin-system"])
admin_router.include_router(services_router, tags=["admin-services"])

# --- Scheduler (unified background jobs) ---
from llm_port_backend.web.api.admin.scheduler.views import router as scheduler_router  # noqa: E402

admin_router.include_router(scheduler_router, prefix="/scheduler", tags=["admin-scheduler"])

# --- Groups management ---
from llm_port_backend.web.api.admin.groups.views import router as groups_router  # noqa: E402

admin_router.include_router(groups_router, prefix="/groups", tags=["admin-groups"])

# --- Auth provider management (OAuth/OIDC SSO) ---
from llm_port_backend.web.api.admin.auth_providers.views import router as auth_providers_router  # noqa: E402

admin_router.include_router(
    auth_providers_router,
    prefix="/auth-providers",
    tags=["admin-auth-providers"],
)

# --- PII dashboard routes (always registered) ----------------------------
# The PII module is toggled at runtime via the services UI, so these proxy
# routes must always be available.  They return 502 if the PII service is
# not running, which the frontend handles gracefully.
from llm_port_backend.web.api.admin.pii.views import router as pii_router  # noqa: E402

admin_router.include_router(pii_router, prefix="/pii", tags=["admin-pii"])

# --- Optional module: RAG ------------------------------------------------
# RAG Lite routes are always mounted (like PII) so settings toggled at
# runtime take effect without restart.  The full RAG Engine takes
# precedence when explicitly enabled via env-var.
if settings.rag_enabled:
    from llm_port_backend.web.api.admin.rag.views import router as rag_router

    admin_router.include_router(rag_router, prefix="/rag", tags=["admin-rag"])
    logger.info("RAG module enabled — /admin/rag routes registered")
else:
    from llm_port_backend.web.api.admin.rag_lite.views import router as rag_lite_router

    admin_router.include_router(rag_lite_router, prefix="/rag", tags=["admin-rag"])
    logger.info("RAG Lite routes registered — activate via Settings > Modules")

# --- Chat & Sessions admin routes (always registered) ---------------------
from llm_port_backend.web.api.admin.chat.views import router as chat_router  # noqa: E402

admin_router.include_router(chat_router, prefix="/chat", tags=["admin-chat"])
