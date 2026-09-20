"""Shared plumbing for the inference observability routes (Phase 6, WI-3).

All three routes are read-only.  They resolve the driver from the deployment's
environment and hand it a live node-control service; the driver owns every
backend-specific detail, so nothing Ray-shaped reaches these handlers.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from starlette import status as http_status

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.inference import InferenceControlPlane, InferenceEnvironment
from llm_port_backend.services.inference.observability import ObservabilityUnsupported
from llm_port_backend.services.inference.registry import registry
from llm_port_backend.services.nodes.service import NodeControlService
from llm_port_backend.settings import settings


def get_node_control_service(
    request: Request,
    dao: NodeControlDAO = Depends(),
) -> NodeControlService:
    """A node-control service wired the same way the runtime log route wires it."""
    llm_service = getattr(request.app.state, "llm_service", None)
    gateway_sync = getattr(llm_service, "gateway_sync", None)
    return NodeControlService(
        dao=dao,
        pepper=settings.settings_master_key,
        enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
        default_command_timeout_sec=settings.node_command_default_timeout_sec,
        gateway_sync=gateway_sync,
    )


async def resolve_driver_for_environment(session: Any, environment: InferenceEnvironment) -> Any:
    """The driver that owns *environment*, or 501 when none is registered."""
    control_plane = await session.get(InferenceControlPlane, environment.control_plane_id)
    key = getattr(control_plane, "driver", None) or "ray"
    driver_cls = registry.get(key)
    if driver_cls is None:
        raise HTTPException(
            status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"no driver registered for {key!r}",
        )
    return driver_cls()


def unsupported(exc: ObservabilityUnsupported) -> HTTPException:
    """Map a driver capability gap onto 501.

    Never an empty page: an operator must be able to tell "this backend cannot
    show you logs" from "this deployment has logged nothing".
    """
    return HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc))
