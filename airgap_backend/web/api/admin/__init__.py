"""Admin API router — aggregates all /admin subroutes."""

from fastapi import APIRouter

from airgap_backend.web.api.admin.audit.views import router as audit_router
from airgap_backend.web.api.admin.containers.views import router as containers_router
from airgap_backend.web.api.admin.images.views import router as images_router
from airgap_backend.web.api.admin.networks.views import router as networks_router
from airgap_backend.web.api.admin.root_mode.views import router as root_mode_router
from airgap_backend.web.api.admin.stacks.views import router as stacks_router

admin_router = APIRouter()
admin_router.include_router(containers_router, prefix="/containers", tags=["admin-containers"])
admin_router.include_router(images_router, prefix="/images", tags=["admin-images"])
admin_router.include_router(stacks_router, prefix="/stacks", tags=["admin-stacks"])
admin_router.include_router(networks_router, prefix="/networks", tags=["admin-networks"])
admin_router.include_router(root_mode_router, prefix="/root-mode", tags=["admin-root-mode"])
admin_router.include_router(audit_router, prefix="/audit", tags=["admin-audit"])
