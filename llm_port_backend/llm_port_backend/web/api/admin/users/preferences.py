"""User preferences endpoints — profile selection and tour progress."""

from __future__ import annotations

import copy
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.user_preferences import UserPreference
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.web.api.admin.users.schema import (
    UserPreferencesRead,
    UserPreferencesUpdate,
)

router = APIRouter()


async def _get_or_create(session: AsyncSession, user_id) -> UserPreference:
    """Return existing preferences row or create a default one."""
    stmt = select(UserPreference).where(UserPreference.user_id == user_id)
    result = await session.execute(stmt)
    pref = result.scalar_one_or_none()
    if pref is not None:
        return pref
    pref = UserPreference(user_id=user_id, preferences={})
    session.add(pref)
    await session.flush()
    return pref


def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge *patch* into *base*, returning a new dict."""
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


@router.get("/me/preferences", response_model=UserPreferencesRead, name="me_preferences")
async def get_preferences(
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> UserPreferencesRead:
    """Return current user's preferences (auto-creates a default row)."""
    pref = await _get_or_create(session, user.id)
    await session.commit()
    return UserPreferencesRead(preferences=pref.preferences)


@router.patch("/me/preferences", response_model=UserPreferencesRead, name="update_me_preferences")
async def update_preferences(
    payload: UserPreferencesUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> UserPreferencesRead:
    """Merge-update the current user's preferences."""
    pref = await _get_or_create(session, user.id)
    pref.preferences = _deep_merge(pref.preferences, payload.preferences)
    await session.commit()
    await session.refresh(pref)
    return UserPreferencesRead(preferences=pref.preferences)
