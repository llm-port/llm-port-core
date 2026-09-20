"""Command dispatch gateway for Ray drivers with short, isolated transactions.

Fixes F01, F02, F21, F22:
- Commits issued commands immediately in a dedicated short session so they are
  visible to the websocket stream dispatcher.
- Reads and polls status using fresh sessions with wall-clock timeout bounds.
- Supports deterministic idempotency keys without random suffixes to ensure
  safe retry and resumption across restarts.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.node_control import (
    InfraNodeCommand,
    NodeCommandStatus,
)
from llm_port_backend.services.nodes.service import NodeControlService

log = logging.getLogger(__name__)

_SUCCESS = NodeCommandStatus.SUCCEEDED.value
_TERMINAL = {
    NodeCommandStatus.SUCCEEDED.value,
    NodeCommandStatus.FAILED.value,
    NodeCommandStatus.CANCELED.value,
    NodeCommandStatus.TIMED_OUT.value,
}


class NodeCommandGateway:
    """Gateway for issuing and observing node commands across short transaction boundaries."""

    def __init__(self, session_or_factory: Any) -> None:
        if isinstance(session_or_factory, NodeCommandGateway):
            self._factory = session_or_factory._factory
            self._direct_service = session_or_factory._direct_service
        elif hasattr(session_or_factory, "issue_command"):
            # Direct service (NodeControlService or duck-typed test double)
            self._factory = None
            self._direct_service = session_or_factory
        elif callable(session_or_factory) and not hasattr(session_or_factory, "execute"):
            self._factory = session_or_factory
            self._direct_service = None
        elif isinstance(session_or_factory, AsyncSession) or hasattr(session_or_factory, "execute"):
            self._factory = None
            self._direct_service = self._get_service(session_or_factory)
        else:
            self._factory = session_or_factory
            self._direct_service = None

    def _get_service(self, session: AsyncSession) -> NodeControlService:
        from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
        from llm_port_backend.settings import settings

        return NodeControlService(
            dao=NodeControlDAO(session),
            pepper=settings.settings_master_key,
            enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
            default_command_timeout_sec=settings.node_command_default_timeout_sec,
        )

    async def issue(
        self,
        *,
        node_id: uuid.UUID | str,
        command_type: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str,
        issued_by: uuid.UUID | None = None,
        correlation_id: str | None = None,
        timeout_sec: int | None = None,
    ) -> InfraNodeCommand:
        """Issue a command and immediately commit if backed by a session factory."""
        node_uuid = node_id if isinstance(node_id, uuid.UUID) else uuid.UUID(str(node_id))
        if self._factory is not None:
            async with self._factory() as session:
                service = self._get_service(session)
                cmd = await service.issue_command(
                    node_id=node_uuid,
                    command_type=command_type,
                    payload=payload or {},
                    idempotency_key=idempotency_key,
                    issued_by=issued_by,
                    correlation_id=correlation_id,
                    timeout_sec=timeout_sec,
                )
                await session.commit()
                return cmd
        elif self._direct_service is not None:
            cmd = await self._direct_service.issue_command(
                node_id=node_uuid,
                command_type=command_type,
                payload=payload or {},
                idempotency_key=idempotency_key,
                issued_by=issued_by,
                correlation_id=correlation_id,
                timeout_sec=timeout_sec,
            )
            if hasattr(self._direct_service, "_dao") and hasattr(self._direct_service._dao, "session"):
                await self._direct_service._dao.session.flush()
            return cmd
        else:
            raise RuntimeError("NodeCommandGateway has no valid session or session factory")

    async def get_command(self, command_id: uuid.UUID | str) -> InfraNodeCommand | None:
        """Fetch command status in an isolated session."""
        cmd_uuid = command_id if isinstance(command_id, uuid.UUID) else uuid.UUID(str(command_id))
        if self._factory is not None:
            async with self._factory() as session:
                service = self._get_service(session)
                return await service.get_command(command_id=cmd_uuid)
        elif self._direct_service is not None:
            return await self._direct_service.get_command(command_id=cmd_uuid)
        return None

    async def wait(
        self,
        command_id: uuid.UUID | str,
        *,
        budget_sec: float,
        poll_interval_sec: float = 1.0,
    ) -> InfraNodeCommand | None:
        """Poll a command until terminal state or wall-clock budget exhaustion."""
        cmd_uuid = command_id if isinstance(command_id, uuid.UUID) else uuid.UUID(str(command_id))
        deadline = asyncio.get_event_loop().time() + budget_sec
        while True:
            cmd = await self.get_command(cmd_uuid)
            if cmd is not None and cmd.status in _TERMINAL:
                return cmd
            if asyncio.get_event_loop().time() >= deadline:
                log.warning("Command %s did not reach terminal state within %.1fs budget", cmd_uuid, budget_sec)
                return None
            await asyncio.sleep(poll_interval_sec)
