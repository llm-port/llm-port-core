"""DAO for node control-plane entities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommand,
    InfraNodeCommandEvent,
    InfraNodeCredential,
    InfraNodeEnrollmentToken,
    InfraNodeEvent,
    InfraNodeInventorySnapshot,
    InfraNodeMaintenanceWindow,
    InfraNodeProfile,
    InfraNodeSession,
    InfraNodeWorkloadAssignment,
    NodeCommandStatus,
)
from llm_port_backend.db.models.system_settings import InfraAgent


class NodeControlDAO:
    """CRUD and query helpers for node orchestration."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create_enrollment_token(
        self,
        *,
        token_hash: str,
        expires_at: datetime,
        issued_by: uuid.UUID | None,
        note: str | None,
    ) -> InfraNodeEnrollmentToken:
        token = InfraNodeEnrollmentToken(
            token_hash=token_hash,
            expires_at=expires_at,
            issued_by=issued_by,
            note=note,
        )
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_usable_enrollment_token(self, *, token_hash: str) -> InfraNodeEnrollmentToken | None:
        now = datetime.now(tz=UTC)
        result = await self.session.execute(
            select(InfraNodeEnrollmentToken).where(
                InfraNodeEnrollmentToken.token_hash == token_hash,
                InfraNodeEnrollmentToken.used_at.is_(None),
                InfraNodeEnrollmentToken.expires_at > now,
            ),
        )
        return result.scalar_one_or_none()

    async def mark_enrollment_token_used(
        self,
        token: InfraNodeEnrollmentToken,
        *,
        node_id: uuid.UUID,
    ) -> None:
        token.used_at = datetime.now(tz=UTC)
        token.used_by_node_id = node_id

    async def get_node_by_id(self, node_id: uuid.UUID) -> InfraNode | None:
        result = await self.session.execute(select(InfraNode).where(InfraNode.id == node_id))
        return result.scalar_one_or_none()

    async def get_node_by_agent_id(self, agent_id: str) -> InfraNode | None:
        result = await self.session.execute(select(InfraNode).where(InfraNode.agent_id == agent_id))
        return result.scalar_one_or_none()

    async def create_node(
        self,
        *,
        agent_id: str,
        host: str,
        version: str | None,
        capabilities_json: dict[str, Any],
        labels_json: dict[str, Any] | None = None,
    ) -> InfraNode:
        now = datetime.now(tz=UTC)
        node = InfraNode(
            agent_id=agent_id,
            host=host,
            version=version,
            capabilities_json=capabilities_json,
            labels_json=labels_json or {},
            last_seen=now,
            status="healthy",
        )
        self.session.add(node)
        await self.session.flush()
        return node

    async def update_node_heartbeat(
        self,
        node: InfraNode,
        *,
        status: str,
        capabilities_json: dict[str, Any] | None = None,
        version: str | None = None,
    ) -> InfraNode:
        node.status = status
        if capabilities_json is not None:
            node.capabilities_json = capabilities_json
        if version is not None:
            node.version = version
        now = datetime.now(tz=UTC)
        node.last_seen = now
        node.updated_at = now  # set explicitly to avoid server-side refresh after flush
        return node

    async def delete_node(self, *, node_id: uuid.UUID) -> bool:
        node = await self.get_node_by_id(node_id)
        if node is None:
            return False
        await self.session.delete(node)
        await self.session.flush()
        return True

    async def list_nodes(self) -> list[InfraNode]:
        result = await self.session.execute(select(InfraNode).order_by(InfraNode.host.asc()))
        return list(result.scalars().all())

    async def list_schedulable_nodes(self) -> list[InfraNode]:
        result = await self.session.execute(
            select(InfraNode).where(
                InfraNode.scheduler_eligible.is_(True),
                InfraNode.maintenance_mode.is_(False),
                InfraNode.draining.is_(False),
                InfraNode.status.in_(["healthy", "degraded"]),
            ),
        )
        return list(result.scalars().all())

    async def create_credential(
        self,
        *,
        node_id: uuid.UUID,
        credential_id: uuid.UUID,
        secret_hash: str,
    ) -> InfraNodeCredential:
        credential = InfraNodeCredential(id=credential_id, node_id=node_id, secret_hash=secret_hash)
        self.session.add(credential)
        await self.session.flush()
        return credential

    async def get_active_credential(self, credential_id: uuid.UUID) -> InfraNodeCredential | None:
        result = await self.session.execute(
            select(InfraNodeCredential).where(
                InfraNodeCredential.id == credential_id,
                InfraNodeCredential.revoked_at.is_(None),
                or_(InfraNodeCredential.expires_at.is_(None), InfraNodeCredential.expires_at > datetime.now(tz=UTC)),
            ),
        )
        return result.scalar_one_or_none()

    async def revoke_credential(self, credential: InfraNodeCredential) -> None:
        credential.revoked_at = datetime.now(tz=UTC)

    async def create_session(self, *, node_id: uuid.UUID, credential_id: uuid.UUID) -> InfraNodeSession:
        session = InfraNodeSession(node_id=node_id, credential_id=credential_id, last_heartbeat_at=datetime.now(tz=UTC))
        self.session.add(session)
        await self.session.flush()
        return session

    async def update_session_offset(self, session: InfraNodeSession, *, offset: int) -> None:
        session.last_rx_offset = offset
        session.last_heartbeat_at = datetime.now(tz=UTC)

    async def close_session(self, session: InfraNodeSession) -> None:
        session.disconnected_at = datetime.now(tz=UTC)

    async def count_active_sessions(self, *, node_id: uuid.UUID) -> int:
        """Return the number of stream sessions still connected for a node."""
        result = await self.session.execute(
            select(func.count())
            .select_from(InfraNodeSession)
            .where(
                InfraNodeSession.node_id == node_id,
                InfraNodeSession.disconnected_at.is_(None),
            ),
        )
        return result.scalar_one()

    async def upsert_inventory_snapshot(
        self,
        *,
        node_id: uuid.UUID,
        inventory_json: dict[str, Any],
        utilization_json: dict[str, Any],
    ) -> InfraNodeInventorySnapshot:
        snap = InfraNodeInventorySnapshot(
            node_id=node_id,
            inventory_json=inventory_json,
            utilization_json=utilization_json,
        )
        self.session.add(snap)
        await self.session.flush()
        return snap

    async def get_latest_inventory_snapshot(self, *, node_id: uuid.UUID) -> InfraNodeInventorySnapshot | None:
        result = await self.session.execute(
            select(InfraNodeInventorySnapshot)
            .where(InfraNodeInventorySnapshot.node_id == node_id)
            .order_by(InfraNodeInventorySnapshot.created_at.desc())
            .limit(1),
        )
        return result.scalar_one_or_none()

    async def create_command(
        self,
        *,
        node_id: uuid.UUID,
        command_type: str,
        payload_json: dict[str, Any],
        idempotency_key: str,
        issued_by: uuid.UUID | None,
        correlation_id: str | None,
        timeout_sec: int | None,
    ) -> InfraNodeCommand:
        command = InfraNodeCommand(
            node_id=node_id,
            command_type=command_type,
            payload_json=payload_json,
            idempotency_key=idempotency_key,
            issued_by=issued_by,
            correlation_id=correlation_id,
            timeout_sec=timeout_sec,
            status=NodeCommandStatus.QUEUED.value,
        )
        self.session.add(command)
        await self.session.flush()
        await self.append_command_event(
            command_id=command.id,
            phase="queued",
            message=f"Command queued: {command_type}",
            payload_json=None,
        )
        return command

    async def get_command_by_idempotency_key(
        self,
        *,
        node_id: uuid.UUID,
        idempotency_key: str,
    ) -> InfraNodeCommand | None:
        result = await self.session.execute(
            select(InfraNodeCommand).where(
                InfraNodeCommand.node_id == node_id,
                InfraNodeCommand.idempotency_key == idempotency_key,
            ),
        )
        return result.scalar_one_or_none()

    async def list_node_commands(self, *, node_id: uuid.UUID, limit: int = 100) -> list[InfraNodeCommand]:
        result = await self.session.execute(
            select(InfraNodeCommand)
            .where(InfraNodeCommand.node_id == node_id)
            .order_by(InfraNodeCommand.issued_at.desc())
            .limit(limit),
        )
        return list(result.scalars().all())

    async def list_pending_commands(self, *, node_id: uuid.UUID, limit: int = 50) -> list[InfraNodeCommand]:
        result = await self.session.execute(
            select(InfraNodeCommand)
            .where(
                InfraNodeCommand.node_id == node_id,
                InfraNodeCommand.status.in_([NodeCommandStatus.QUEUED.value, NodeCommandStatus.DISPATCHED.value]),
            )
            .order_by(InfraNodeCommand.issued_at.asc())
            .limit(limit),
        )
        return list(result.scalars().all())

    async def get_command(self, command_id: uuid.UUID) -> InfraNodeCommand | None:
        result = await self.session.execute(select(InfraNodeCommand).where(InfraNodeCommand.id == command_id))
        return result.scalar_one_or_none()

    async def set_command_status(
        self,
        command: InfraNodeCommand,
        *,
        status: NodeCommandStatus,
        result_json: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> InfraNodeCommand:
        now = datetime.now(tz=UTC)
        command.status = status.value
        if status == NodeCommandStatus.DISPATCHED and command.dispatched_at is None:
            command.dispatched_at = now
        if status == NodeCommandStatus.ACKED and command.acked_at is None:
            command.acked_at = now
        if status == NodeCommandStatus.RUNNING and command.started_at is None:
            command.started_at = now
        if status in {
            NodeCommandStatus.SUCCEEDED,
            NodeCommandStatus.FAILED,
            NodeCommandStatus.CANCELED,
            NodeCommandStatus.TIMED_OUT,
        }:
            command.completed_at = now
        if result_json is not None:
            command.result_json = result_json
        command.error_code = error_code
        command.error_message = error_message
        return command

    async def append_command_event(
        self,
        *,
        command_id: uuid.UUID,
        phase: str,
        message: str,
        payload_json: dict[str, Any] | None,
    ) -> InfraNodeCommandEvent:
        count_stmt = select(func.coalesce(func.max(InfraNodeCommandEvent.seq), 0)).where(
            InfraNodeCommandEvent.command_id == command_id,
        )
        seq_res = await self.session.execute(count_stmt)
        next_seq = int(seq_res.scalar_one() or 0) + 1
        event = InfraNodeCommandEvent(
            command_id=command_id,
            seq=next_seq,
            phase=phase,
            message=message,
            payload_json=payload_json,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_command_events(self, *, command_id: uuid.UUID) -> list[InfraNodeCommandEvent]:
        result = await self.session.execute(
            select(InfraNodeCommandEvent)
            .where(InfraNodeCommandEvent.command_id == command_id)
            .order_by(InfraNodeCommandEvent.seq.asc()),
        )
        return list(result.scalars().all())

    async def add_node_events(self, *, node_id: uuid.UUID, events: list[dict[str, Any]]) -> None:
        rows = [
            InfraNodeEvent(
                node_id=node_id,
                event_type=str(item.get("event_type") or item.get("type") or "event"),
                severity=str(item.get("severity") or "info"),
                correlation_id=item.get("correlation_id"),
                payload_json=item,
            )
            for item in events
        ]
        self.session.add_all(rows)
        await self.session.flush()

    async def set_node_maintenance(
        self,
        *,
        node: InfraNode,
        enabled: bool,
        reason: str | None,
        requested_by: uuid.UUID | None,
    ) -> None:
        node.maintenance_mode = enabled
        node.status = "maintenance" if enabled else "healthy"
        if enabled:
            window = InfraNodeMaintenanceWindow(
                node_id=node.id,
                reason=reason,
                requested_by=requested_by,
                state="active",
            )
            self.session.add(window)
            await self.session.flush()
        else:
            active = await self.session.execute(
                select(InfraNodeMaintenanceWindow).where(
                    InfraNodeMaintenanceWindow.node_id == node.id,
                    InfraNodeMaintenanceWindow.state == "active",
                    InfraNodeMaintenanceWindow.ended_at.is_(None),
                ),
            )
            for row in active.scalars().all():
                row.state = "ended"
                row.ended_at = datetime.now(tz=UTC)

    async def set_node_draining(self, *, node: InfraNode, enabled: bool) -> None:
        node.draining = enabled
        if enabled:
            node.status = "draining"
        elif node.status == "draining":
            node.status = "healthy"

    async def upsert_workload_assignment(
        self,
        *,
        runtime_id: uuid.UUID,
        node_id: uuid.UUID,
        desired_state: str,
        actual_state: str,
    ) -> InfraNodeWorkloadAssignment:
        result = await self.session.execute(
            select(InfraNodeWorkloadAssignment).where(InfraNodeWorkloadAssignment.runtime_id == runtime_id),
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = InfraNodeWorkloadAssignment(
                runtime_id=runtime_id,
                node_id=node_id,
                desired_state=desired_state,
                actual_state=actual_state,
            )
            self.session.add(row)
            await self.session.flush()
            return row
        row.node_id = node_id
        row.desired_state = desired_state
        row.actual_state = actual_state
        return row

    async def sync_legacy_infra_agent(self, *, node: InfraNode) -> None:
        result = await self.session.execute(select(InfraAgent).where(InfraAgent.id == node.agent_id))
        existing = result.scalar_one_or_none()
        if existing is None:
            existing = InfraAgent(
                id=node.agent_id,
                host=node.host,
                status=node.status,
                capabilities=node.capabilities_json,
                version=node.version,
                last_seen=node.last_seen,
            )
            self.session.add(existing)
            return
        existing.host = node.host
        existing.status = node.status
        existing.capabilities = node.capabilities_json
        existing.version = node.version
        existing.last_seen = node.last_seen

    # ── profile CRUD ──────────────────────────────────────────

    async def create_profile(
        self,
        *,
        name: str,
        description: str | None = None,
        is_default: bool = False,
        runtime_config: dict[str, Any] | None = None,
        gpu_config: dict[str, Any] | None = None,
        storage_config: dict[str, Any] | None = None,
        network_config: dict[str, Any] | None = None,
        logging_config: dict[str, Any] | None = None,
        security_config: dict[str, Any] | None = None,
        update_config: dict[str, Any] | None = None,
    ) -> InfraNodeProfile:
        if is_default:
            await self._clear_default_profile()
        profile = InfraNodeProfile(
            name=name,
            description=description,
            is_default=is_default,
            runtime_config=runtime_config or {},
            gpu_config=gpu_config or {},
            storage_config=storage_config or {},
            network_config=network_config or {},
            logging_config=logging_config or {},
            security_config=security_config or {},
            update_config=update_config or {},
        )
        self.session.add(profile)
        await self.session.flush()
        return profile

    async def get_profile(self, profile_id: uuid.UUID) -> InfraNodeProfile | None:
        result = await self.session.execute(
            select(InfraNodeProfile).where(InfraNodeProfile.id == profile_id),
        )
        return result.scalar_one_or_none()

    async def get_profile_by_name(self, name: str) -> InfraNodeProfile | None:
        result = await self.session.execute(
            select(InfraNodeProfile).where(InfraNodeProfile.name == name),
        )
        return result.scalar_one_or_none()

    async def list_profiles(self) -> list[InfraNodeProfile]:
        result = await self.session.execute(
            select(InfraNodeProfile).order_by(InfraNodeProfile.name.asc()),
        )
        return list(result.scalars().all())

    async def update_profile(
        self,
        profile: InfraNodeProfile,
        *,
        updates: dict[str, Any],
    ) -> InfraNodeProfile:
        allowed = {
            "name", "description", "is_default",
            "runtime_config", "gpu_config", "storage_config",
            "network_config", "logging_config", "security_config",
            "update_config",
        }
        if updates.get("is_default"):
            await self._clear_default_profile()
        for key, value in updates.items():
            if key in allowed:
                setattr(profile, key, value)
        return profile

    async def delete_profile(self, profile_id: uuid.UUID) -> bool:
        profile = await self.get_profile(profile_id)
        if profile is None:
            return False
        # Unassign any nodes pointing to this profile
        nodes = await self.session.execute(
            select(InfraNode).where(InfraNode.profile_id == profile_id),
        )
        for node in nodes.scalars().all():
            node.profile_id = None
        await self.session.delete(profile)
        await self.session.flush()
        return True

    async def assign_profile(self, *, node_id: uuid.UUID, profile_id: uuid.UUID) -> InfraNode:
        node = await self.get_node_by_id(node_id)
        if node is None:
            raise ValueError("Node not found.")
        profile = await self.get_profile(profile_id)
        if profile is None:
            raise ValueError("Profile not found.")
        node.profile_id = profile_id
        return node

    async def unassign_profile(self, *, node_id: uuid.UUID) -> InfraNode:
        node = await self.get_node_by_id(node_id)
        if node is None:
            raise ValueError("Node not found.")
        node.profile_id = None
        return node

    async def _clear_default_profile(self) -> None:
        """Ensure only one profile has is_default=True."""
        result = await self.session.execute(
            select(InfraNodeProfile).where(InfraNodeProfile.is_default.is_(True)),
        )
        for p in result.scalars().all():
            p.is_default = False
