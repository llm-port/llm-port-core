"""Shared RBAC dependencies for route-level permission checks."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException
from starlette import status

from airgap_backend.db.dao.rbac_dao import RbacDAO
from airgap_backend.db.models.users import User, current_active_user


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
