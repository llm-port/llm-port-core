"""Backfill RBAC default_user role for existing non-superusers without roles.

Usage:
    python scripts/backfill_default_user_role.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.models.rbac import UserRole
from llm_port_backend.db.models.users import User
from llm_port_backend.settings import settings


async def main() -> None:
    engine = create_async_engine(str(settings.db_url), echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            rbac_dao = RbacDAO(session)

            default_role = await rbac_dao.get_role_by_name("default_user")
            if default_role is None:
                await rbac_dao.seed_defaults()
                default_role = await rbac_dao.get_role_by_name("default_user")
                if default_role is None:
                    raise RuntimeError(
                        "Built-in role 'default_user' was not found after RBAC seeding.",
                    )

            result = await session.execute(
                select(User.id, User.email)
                .outerjoin(UserRole, UserRole.user_id == User.id)
                .where(User.is_superuser.is_(False))
                .group_by(User.id, User.email)
                .having(func.count(UserRole.role_id) == 0),
            )
            targets = result.all()

            for user_id, _email in targets:
                await rbac_dao.assign_role(user_id, default_role.id)

            await session.commit()

            print(f"default_user role id: {default_role.id}")
            print(f"users updated: {len(targets)}")
            if targets:
                for _, email in targets[:20]:
                    print(f" - {email}")
                if len(targets) > 20:
                    print(f" ... and {len(targets) - 20} more")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
