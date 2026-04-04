"""Shared RBAC dependencies for route-level permission checks."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from starlette import status

from llm_port_backend.db.dao.rag_grants_dao import RagGrantDAO
from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.models.users import User, current_active_user


def require_permission(
    resource: str,
    action: str,
) -> Callable[..., Coroutine[Any, Any, User]]:
    """
    Factory that returns a FastAPI dependency enforcing a permission check.

    Superusers bypass all checks (backward-compatible).

    Usage::

        @router.get("/providers")
        async def list_providers(
            user: User = Depends(require_permission("llm.providers", "read")),
        ):
            ...
    """

    async def _guard(
        user: Annotated[User, Depends(current_active_user)],
        rbac_dao: RbacDAO = Depends(),
    ) -> User:
        # Superusers always pass
        if user.is_superuser:
            return user

        has = await rbac_dao.has_permission(user.id, resource, action)
        if not has:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {resource}:{action}",
            )
        return user

    return _guard


def require_rag_container_action(
    action: str,
    container_param: str,
) -> Callable[..., Coroutine[Any, Any, User]]:
    """
    Enforce container-scoped RAG action.

    This helper is used on endpoints that operate on a specific container id.
    """

    async def _guard(
        request: Request,
        user: Annotated[User, Depends(current_active_user)],
        rbac_dao: RbacDAO = Depends(),
        grant_dao: RagGrantDAO = Depends(),
    ) -> User:
        if user.is_superuser:
            return user
        has_global = await rbac_dao.has_permission(user.id, "rag.containers", action)
        if not has_global:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: rag.containers:{action}",
            )
        container_id = request.path_params.get(container_param)
        if not container_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing container path parameter: {container_param}",
            )
        has_grant = await grant_dao.has_container_action(
            user_id=user.id,
            container_id=container_id,
            action=action,
        )
        if not has_grant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Container grant required for action '{action}' on container '{container_id}'.",
            )
        return user

    return _guard
