"""A user's usage group: set by an admin, shown on the user, cleared with null.

The group is what the gateway stamps on every request-log row for that user,
so usage can be reported per team. Membership stays RBAC; this is attribution.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.models.groups import Group
from llm_port_backend.db.models.users import User, current_active_user


async def _seed_user(dbsession: AsyncSession, *, email: str, is_superuser: bool) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="not-a-real-hash",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=is_superuser,
    )
    dbsession.add(user)
    await dbsession.flush()
    return user


async def _seed_group(dbsession: AsyncSession, name: str) -> Group:
    group = Group(name=name, description=None)
    dbsession.add(group)
    await dbsession.flush()
    return group


@pytest.fixture
async def superuser(dbsession: AsyncSession) -> User:
    return await _seed_user(dbsession, email="admin-ug@example.com", is_superuser=True)


@pytest.fixture
def authed_app(fastapi_app: FastAPI, superuser: User) -> FastAPI:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    return fastapi_app


async def test_the_group_is_set_shown_and_cleared(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    target = await _seed_user(dbsession, email="dev@example.com", is_superuser=False)
    finance = await _seed_group(dbsession, f"finance-{uuid.uuid4().hex[:8]}")
    url = authed_app.url_path_for("set_user_usage_group", user_id=str(target.id))

    resp = await client.put(url, json={"usage_group_id": str(finance.id)})
    assert resp.status_code == status.HTTP_200_OK, resp.text
    body = resp.json()
    assert body["usage_group_id"] == str(finance.id)
    assert body["usage_group_name"] == finance.name

    # The list every admin page reads shows it too.
    listed = await client.get(authed_app.url_path_for("list_users_with_roles"))
    assert listed.status_code == status.HTTP_200_OK
    row = next(u for u in listed.json() if u["id"] == str(target.id))
    assert row["usage_group_name"] == finance.name

    # null clears it.
    resp = await client.put(url, json={"usage_group_id": None})
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json()["usage_group_id"] is None
    assert resp.json()["usage_group_name"] is None


async def test_an_unknown_group_is_refused(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    target = await _seed_user(dbsession, email="dev2@example.com", is_superuser=False)
    url = authed_app.url_path_for("set_user_usage_group", user_id=str(target.id))

    resp = await client.put(url, json={"usage_group_id": str(uuid.uuid4())})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unknown group" in resp.json()["detail"]


async def test_an_unknown_user_is_not_found(
    authed_app: FastAPI,
    client: AsyncClient,
) -> None:
    url = authed_app.url_path_for("set_user_usage_group", user_id=str(uuid.uuid4()))
    resp = await client.put(url, json={"usage_group_id": None})
    assert resp.status_code == status.HTTP_404_NOT_FOUND
