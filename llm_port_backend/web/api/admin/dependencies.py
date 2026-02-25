"""Shared FastAPI dependencies for the /admin namespace."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.container_registry_dao import ContainerRegistryDAO
from llm_port_backend.db.dao.root_session_dao import RootSessionDAO
from llm_port_backend.db.models.containers import (
    AuditResult,
    ContainerClass,
    ContainerPolicy,
    ContainerRegistry,
)
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.policy.enforcement import PolicyEnforcer


def get_docker(request: Request) -> DockerService:
    """Retrieve the shared DockerService from app state."""
    return request.app.state.docker  # type: ignore[no-any-return]


def get_policy_enforcer() -> PolicyEnforcer:
    """Return a stateless PolicyEnforcer instance."""
    return PolicyEnforcer()


async def require_superuser(
    user: Annotated[User, Depends(current_active_user)],
) -> User:
    """Ensure the current user is a superuser (admin)."""
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return user


async def get_root_mode_active(
    user: Annotated[User, Depends(require_superuser)],
    root_dao: RootSessionDAO = Depends(),
) -> bool:
    """Return True if the current user has a valid active root session."""
    session = await root_dao.get_active(user.id)
    return session is not None


async def get_registry_entry(
    container_id: str,
    registry_dao: ContainerRegistryDAO = Depends(),
) -> ContainerRegistry:
    """Fetch a container registry entry or return a default UNTRUSTED entry."""
    entry = await registry_dao.get(container_id)
    if entry is None:
        # Unknown containers are treated as UNTRUSTED with restricted policy
        return ContainerRegistry(
            container_id=container_id,
            name=container_id,
            container_class=ContainerClass.UNTRUSTED,
            owner_scope="unknown",
            policy=ContainerPolicy.RESTRICTED,
            engine_id="local",
        )
    return entry


async def audit_action(
    action: str,
    target_type: str,
    target_id: str,
    result: AuditResult,
    actor_id: uuid.UUID | None,
    severity: str,
    audit_dao: AuditDAO,
    metadata_json: str | None = None,
) -> None:
    """Helper that logs an audit event and flushes it."""
    await audit_dao.log(
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        actor_id=actor_id,
        severity=severity,
        metadata_json=metadata_json,
    )
