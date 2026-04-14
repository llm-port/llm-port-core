"""Tests for RBAC enforcement on chat proxy tool-policy endpoints.

RED tests first — non-superusers without ``chat.tool_policy`` permission
should get 403, but currently get 5xx because RBAC is missing.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette import status

from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.web.application import get_app


def _make_user(*, superuser: bool) -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = superuser
    user.is_verified = True
    return user


def _make_rbac_dao(*, allow: bool = False) -> RbacDAO:
    """Create a mock RbacDAO that approves or denies all permission checks."""
    dao = MagicMock(spec=RbacDAO)
    dao.has_permission = AsyncMock(return_value=allow)
    return dao


@pytest.fixture
def app_no_db():
    """FastAPI app with DB session stubbed out (no PostgreSQL needed)."""
    application = get_app()
    application.dependency_overrides[get_db_session] = lambda: MagicMock()
    return application


@pytest.mark.anyio
async def test_get_tool_policy_forbidden_without_permission(app_no_db) -> None:
    """Non-superuser without chat.tool_policy:read must get 403."""
    app_no_db.dependency_overrides[current_active_user] = lambda: _make_user(
        superuser=False,
    )
    # RbacDAO always denies
    app_no_db.dependency_overrides[RbacDAO] = lambda: _make_rbac_dao(allow=False)

    sid = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app_no_db),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/api/chat/sessions/{sid}/tool-policy",
            cookies={"fapiauth": "fake-jwt-for-test"},
        )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_patch_tool_policy_forbidden_without_permission(app_no_db) -> None:
    """Non-superuser without chat.tool_policy:update must get 403."""
    app_no_db.dependency_overrides[current_active_user] = lambda: _make_user(
        superuser=False,
    )
    app_no_db.dependency_overrides[RbacDAO] = lambda: _make_rbac_dao(allow=False)

    sid = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app_no_db),
        base_url="http://test",
    ) as client:
        response = await client.patch(
            f"/api/chat/sessions/{sid}/tool-policy",
            json={"execution_policy": "auto"},
            cookies={"fapiauth": "fake-jwt-for-test"},
        )
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── PII-policy RBAC tests ───────────────────────────────────────


@pytest.mark.anyio
async def test_get_pii_policy_forbidden_without_permission(app_no_db) -> None:
    """Non-superuser without pii.session:read must get 403."""
    app_no_db.dependency_overrides[current_active_user] = lambda: _make_user(
        superuser=False,
    )
    app_no_db.dependency_overrides[RbacDAO] = lambda: _make_rbac_dao(allow=False)

    sid = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app_no_db),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/api/chat/sessions/{sid}/pii-policy",
            cookies={"fapiauth": "fake-jwt-for-test"},
        )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_patch_pii_policy_forbidden_without_permission(app_no_db) -> None:
    """Non-superuser without pii.session:strengthen must get 403."""
    app_no_db.dependency_overrides[current_active_user] = lambda: _make_user(
        superuser=False,
    )
    app_no_db.dependency_overrides[RbacDAO] = lambda: _make_rbac_dao(allow=False)

    sid = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app_no_db),
        base_url="http://test",
    ) as client:
        response = await client.patch(
            f"/api/chat/sessions/{sid}/pii-policy",
            json={"egress_fail_action": "block"},
            cookies={"fapiauth": "fake-jwt-for-test"},
        )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_delete_pii_policy_forbidden_without_permission(app_no_db) -> None:
    """Non-superuser without pii.session:strengthen must get 403."""
    app_no_db.dependency_overrides[current_active_user] = lambda: _make_user(
        superuser=False,
    )
    app_no_db.dependency_overrides[RbacDAO] = lambda: _make_rbac_dao(allow=False)

    sid = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app_no_db),
        base_url="http://test",
    ) as client:
        response = await client.delete(
            f"/api/chat/sessions/{sid}/pii-policy",
            cookies={"fapiauth": "fake-jwt-for-test"},
        )
    assert response.status_code == status.HTTP_403_FORBIDDEN
